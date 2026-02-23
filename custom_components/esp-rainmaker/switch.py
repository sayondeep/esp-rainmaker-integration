from homeassistant.components.switch import SwitchEntity
from homeassistant.helpers.entity import DeviceInfo
from .const import DOMAIN
import logging

_LOGGER = logging.getLogger(__name__)

async def async_setup_entry(hass, entry, async_add_entities):
    entry_data = hass.data[DOMAIN][entry.entry_id]
    ws_client = entry_data["ws_client"]

    # Create switch entities for RainMaker socket devices
    switches = []

    # Try to get RainMaker nodes and create switch entities
    try:
        # Get devices list via WebSocket
        response = await ws_client.send_request("rainmakernodes")
        devices = response.get("devices", [])

        for device in devices:
            node_id = device.get("node_id")
            if node_id:
                # Get detailed device info to check for socket capabilities
                detail_response = await ws_client.send_request("nodedetails", {"node_id": node_id})
                node_details_data = detail_response.get("details", {})
                node_details = node_details_data.get("node_details", [])

                for node_detail in node_details:
                    # Check if this node has socket devices
                    config = node_detail.get("config", {})
                    devices_config = config.get("devices", [])

                    # Find all socket devices in this node
                    for device_config in devices_config:
                        if device_config.get("type") == "esp.device.socket":
                            device_name = device_config.get("name", "Socket")

                            # Check if this socket has Power parameter defined in config
                            # (it may not be in params yet if device hasn't reported state)
                            has_power_param = any(
                                p.get("name") == "Power" and p.get("type") == "esp.param.power"
                                for p in device_config.get("params", [])
                            )

                            if has_power_param:
                                switches.append(RainMakerSocket(hass, ws_client, device, node_detail, device_name))
                                _LOGGER.debug(f"Found socket device: {device_name} in node {node_id}")

        _LOGGER.info(f"Found {len(switches)} ESP RainMaker sockets")
    except Exception as e:
        _LOGGER.warning(f"Could not fetch RainMaker nodes during setup: {e}")

    async_add_entities(switches, True)

class RainMakerSocket(SwitchEntity):
    def __init__(self, hass, ws_client, device_data, node_detail, socket_device_name):
        self._hass = hass
        self._ws_client = ws_client
        self._device_data = device_data
        self._node_detail = node_detail
        self._node_id = device_data["node_id"]
        self._socket_device_name = socket_device_name

        # Get socket parameters from node_detail
        params = node_detail.get("params", {})
        socket_params = params.get(socket_device_name, {})

        # Get device info from node_detail
        node_name = node_detail.get("name", device_data.get("name", f"RainMaker Device {self._node_id[:8]}"))
        self._device_name = node_name

        self._model = node_detail.get("model", "Unknown")
        self._fw_version = node_detail.get("fw_version", "Unknown")

        # Use socket device name for entity name
        self._attr_name = socket_device_name
        self._attr_unique_id = f"esp_rainmaker_socket_{self._node_id}_{socket_device_name}"

        # Set initial state from parameters
        self._attr_is_on = socket_params.get("Power", False)

        # Set faster polling for more responsive updates (10 seconds instead of 30)
        from datetime import timedelta
        self._attr_scan_interval = timedelta(seconds=10)

    @property
    def name(self):
        """Return the name of the switch entity."""
        return self._attr_name

    @property
    def device_info(self):
        return DeviceInfo(
            identifiers={(DOMAIN, f"rainmaker_{self._node_id}")},
            name=self._device_name,
            manufacturer="Espressif",
            model=self._model,
            sw_version=self._fw_version,
        )

    @property
    def extra_state_attributes(self):
        """Return additional attributes."""
        params = self._node_detail.get("params", {})
        socket_params = params.get(self._socket_device_name, {})

        return {
            "node_id": self._node_id,
            "device_type": "rainmaker_socket",
            "socket_name": self._socket_device_name,
            "model": self._model,
            "firmware_version": self._fw_version,
            "childlock": socket_params.get("childlock", False),
        }

    async def async_turn_on(self, **kwargs):
        """Turn on the socket."""
        socket_data = {"Power": True}
        await self._send_command(socket_data, "turn on")

    async def async_turn_off(self, **kwargs):
        """Turn off the socket."""
        socket_data = {"Power": False}
        await self._send_command(socket_data, "turn off")

    async def _send_command(self, socket_data, action_description):
        """Send command to ESP RainMaker device via WebSocket."""
        try:
            # The payload uses the socket device name as the key
            payload = {self._socket_device_name: socket_data}

            response = await self._ws_client.send_request(
                "setparams",
                {"node_id": self._node_id, "data": payload}
            )

            # Check if device is offline
            if response.get("offline", False):
                _LOGGER.warning(f"Cannot {action_description} {self._attr_name}: device is offline")
                return

            if response.get("success", False):
                # Update local state immediately for instant UI feedback
                if "Power" in socket_data:
                    self._attr_is_on = socket_data["Power"]

                # Create detailed log message
                params_str = ", ".join([f"{k}={v}" for k, v in socket_data.items()])
                _LOGGER.info(f"Successfully {action_description} {self._attr_name}: {params_str}")

                # Immediate state update for UI responsiveness
                self.async_write_ha_state()

                # Schedule a delayed refresh to get actual device state (2 seconds later)
                self._hass.loop.call_later(2.0, lambda: self._hass.async_create_task(self.async_update()))

            else:
                error_msg = response.get('error', 'Unknown error')
                # Don't log errors for offline devices - they're expected
                if "offline" not in error_msg.lower():
                    _LOGGER.error(f"Failed to {action_description} {self._attr_name}: {error_msg}")
        except Exception as e:
            error_msg = str(e).lower()
            # Don't log errors for offline devices - they're expected
            if "offline" not in error_msg and "could not connect" not in error_msg:
                _LOGGER.error(f"Error during {action_description} {self._attr_name}: {e}")

    async def async_update(self):
        """Update the socket state using efficient getparams endpoint via WebSocket."""
        try:
            response = await self._ws_client.send_request(
                "getparams",
                {"node_id": self._node_id}
            )

            # Check if device is offline
            if response.get("offline", False):
                _LOGGER.debug(f"Device {self._attr_name} is offline, skipping update")
                return

            params = response.get("params", {})

            if self._socket_device_name in params:
                socket_params = params[self._socket_device_name]

                # Update socket state
                self._attr_is_on = socket_params.get("Power", False)

                _LOGGER.debug(f"Updated {self._attr_name}: Power={self._attr_is_on}")
            else:
                _LOGGER.warning(f"No parameters found for socket {self._socket_device_name} in node {self._node_id}")

        except Exception as e:
            error_msg = str(e).lower()
            # Don't log errors for offline devices - they're expected
            if "offline" not in error_msg and "could not connect" not in error_msg:
                _LOGGER.error(f"Error updating RainMaker socket {self._node_id}/{self._socket_device_name}: {e}")
