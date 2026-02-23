from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers import device_registry as dr
from .const import DOMAIN
import logging
import asyncio
import json
import aiohttp
from typing import Optional, Dict, Any, Callable

_LOGGER = logging.getLogger(__name__)

PLATFORMS = ["sensor", "light", "switch"]


class WebSocketClient:
    """WebSocket client for ESP RainMaker addon communication"""

    def __init__(self, hass: HomeAssistant, host: str, port: int):
        self.hass = hass
        self.host = host
        self.port = port
        self.ws_url = f"ws://{host}:{port}"
        self.websocket: Optional[aiohttp.ClientWebSocketResponse] = None
        self.session: Optional[aiohttp.ClientSession] = None
        self._message_id = 0
        self._pending_requests: Dict[int, asyncio.Future] = {}
        self._reconnect_task: Optional[asyncio.Task] = None
        self._connected = False
        self._should_reconnect = True
        self._listeners: Dict[str, list] = {}  # Event type -> list of callbacks

    async def connect(self):
        """Connect to WebSocket server"""
        if self.websocket and not self.websocket.closed:
            return True

        try:
            if not self.session:
                self.session = aiohttp.ClientSession()

            _LOGGER.info(f"Connecting to WebSocket server at {self.ws_url}")
            self.websocket = await self.session.ws_connect(self.ws_url)
            self._connected = True
            _LOGGER.info("WebSocket connected successfully")

            # Start message listener
            self.hass.async_create_task(self._listen_messages())

            return True
        except Exception as e:
            _LOGGER.error(f"Failed to connect to WebSocket server: {e}")
            self._connected = False
            return False

    async def disconnect(self):
        """Disconnect from WebSocket server"""
        self._should_reconnect = False
        if self._reconnect_task:
            self._reconnect_task.cancel()
            self._reconnect_task = None

        if self.websocket:
            await self.websocket.close()
            self.websocket = None

        if self.session:
            await self.session.close()
            self.session = None

        self._connected = False

    async def _listen_messages(self):
        """Listen for incoming WebSocket messages"""
        try:
            if not self.websocket:
                return

            async for msg in self.websocket:
                if msg.type == aiohttp.WSMsgType.TEXT:
                    try:
                        data = json.loads(msg.data)
                        msg_id = data.get("id")
                        msg_type = data.get("type")
                        payload = data.get("payload", {})

                        # Handle response to pending request
                        if msg_id in self._pending_requests:
                            future = self._pending_requests.pop(msg_id)
                            if not future.done():
                                future.set_result(payload)

                        # Handle event notifications (if we add them later)
                        if msg_type == "event" and msg_type in self._listeners:
                            for callback in self._listeners[msg_type]:
                                try:
                                    await callback(payload)
                                except Exception as e:
                                    _LOGGER.error(f"Error in event callback: {e}")

                    except json.JSONDecodeError as e:
                        _LOGGER.error(f"Failed to parse WebSocket message: {e}")
                    except Exception as e:
                        _LOGGER.error(f"Error processing WebSocket message: {e}")

                elif msg.type == aiohttp.WSMsgType.ERROR:
                    _LOGGER.error(f"WebSocket error: {self.websocket.exception() if self.websocket else 'Unknown error'}")
                    break
                elif msg.type == aiohttp.WSMsgType.CLOSE:
                    _LOGGER.warning("WebSocket connection closed")
                    break
                elif msg.type == aiohttp.WSMsgType.CLOSED:
                    _LOGGER.warning("WebSocket connection already closed")
                    break

        except Exception as e:
            _LOGGER.error(f"WebSocket listener error: {e}")
        finally:
            self._connected = False
            if self._should_reconnect:
                self._reconnect_task = self.hass.async_create_task(self._reconnect())

    async def _reconnect(self):
        """Reconnect to WebSocket server with exponential backoff"""
        retry_delay = 1
        max_delay = 60

        while self._should_reconnect:
            await asyncio.sleep(retry_delay)
            _LOGGER.info(f"Attempting to reconnect to WebSocket server (delay: {retry_delay}s)")

            if await self.connect():
                _LOGGER.info("WebSocket reconnected successfully")
                return
            else:
                retry_delay = min(retry_delay * 2, max_delay)

    async def send_request(self, msg_type: str, payload: Dict[str, Any] = None, timeout: float = 30.0) -> Dict[str, Any]:
        """Send a request and wait for response"""
        if not self._connected or not self.websocket or self.websocket.closed:
            if not await self.connect():
                raise ConnectionError("Not connected to WebSocket server")

        self._message_id += 1
        msg_id = self._message_id

        message = {
            "id": msg_id,
            "type": msg_type,
            "payload": payload or {}
        }

        future = asyncio.Future()
        self._pending_requests[msg_id] = future

        try:
            if not self.websocket or self.websocket.closed:
                raise ConnectionError("WebSocket connection lost")

            await self.websocket.send_str(json.dumps(message))

            # Wait for response with timeout
            try:
                result = await asyncio.wait_for(future, timeout=timeout)
                return result
            except asyncio.TimeoutError:
                self._pending_requests.pop(msg_id, None)
                raise TimeoutError(f"Request {msg_type} timed out after {timeout}s")

        except Exception as e:
            self._pending_requests.pop(msg_id, None)
            # Mark as disconnected if connection error
            if isinstance(e, (ConnectionError, aiohttp.ClientError)):
                self._connected = False
            raise

    def subscribe(self, event_type: str, callback: Callable):
        """Subscribe to event notifications"""
        if event_type not in self._listeners:
            self._listeners[event_type] = []
        self._listeners[event_type].append(callback)

    def unsubscribe(self, event_type: str, callback: Callable):
        """Unsubscribe from event notifications"""
        if event_type in self._listeners:
            try:
                self._listeners[event_type].remove(callback)
            except ValueError:
                pass

    @property
    def is_connected(self) -> bool:
        """Check if WebSocket is connected"""
        return self._connected and self.websocket and not self.websocket.closed


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    hass.data.setdefault(DOMAIN, {})

    host = entry.data["host"]
    port = entry.data["port"]

    # Create WebSocket client
    ws_client = WebSocketClient(hass, host, port)

    # Connect to WebSocket server
    if not await ws_client.connect():
        _LOGGER.error("Failed to connect to ESP RainMaker WebSocket server")
        return False

    hass.data[DOMAIN][entry.entry_id] = {
        "host": host,
        "port": port,
        "ws_client": ws_client,
    }

    # Register service to force device name refresh
    async def force_device_name_refresh(call: ServiceCall):
        """Service to force refresh of device names from ESP RainMaker."""
        _LOGGER.info("Force device name refresh service called")

        # Get all ESP RainMaker light entities and trigger name refresh
        from homeassistant.helpers.entity_registry import async_get as async_get_entity_registry
        entity_registry = async_get_entity_registry(hass)

        for entity_id, entity_entry in entity_registry.entities.items():
            if entity_entry.platform == DOMAIN and entity_id.startswith("light."):
                # Get the entity and trigger a name refresh
                entity = hass.states.get(entity_id)
                if entity:
                    # Trigger an update for this entity
                    await hass.services.async_call(
                        "homeassistant",
                        "update_entity",
                        {"entity_id": entity_id}
                    )

        _LOGGER.info("Device name refresh completed")

    # Register the service
    hass.services.async_register(
        DOMAIN,
        "refresh_device_names",
        force_device_name_refresh,
        schema=None
    )

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True

async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        entry_data = hass.data[DOMAIN].get(entry.entry_id)
        if entry_data and "ws_client" in entry_data:
            await entry_data["ws_client"].disconnect()
        hass.data[DOMAIN].pop(entry.entry_id)

        # Remove the service
        hass.services.async_remove(DOMAIN, "refresh_device_names")
    return unload_ok
