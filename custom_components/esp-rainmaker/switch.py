from homeassistant.components.switch import SwitchEntity
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.entity import DeviceInfo
from .const import DOMAIN
import logging

_LOGGER = logging.getLogger(__name__)

async def async_setup_entry(hass, entry, async_add_entities):
    host = entry.data["host"]
    port = entry.data["port"]
    url = f"http://{host}:{port}/rainmakernodes"

    # Create switch entities for RainMaker socket devices
    switches = []

    # Try to get RainMaker nodes and create switch entities
    try:
        session = async_get_clientsession(hass)
        async with session.get(url) as resp:
            if resp.status == 200:
                data = await resp.json()
                devices = data.get("devices", [])

                for device in devices:
                    node_id = device.get("node_id")
                    if node_id:
                        # Get detailed device info to check for socket capabilities
                        detail_url = f"http://{host}:{port}/nodedetails/{node_id}"
                        async with session.get(detail_url) as detail_resp:
                            if detail_resp.status == 200:
                                detail_data = await detail_resp.json()
                                node_details = detail_data.get("details", {}).get("node_details", [])

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
                                                switches.append(RainMakerSocket(hass, f"http://{host}:{port}", device, node_detail, device_name))
                                                _LOGGER.debug(f"Found socket device: {device_name} in node {node_id}")

                _LOGGER.info(f"Found {len(switches)} ESP RainMaker sockets")
            else:
                _LOGGER.error(f"Failed to fetch RainMaker nodes: HTTP {resp.status}")
    except Exception as e:
        _LOGGER.warning(f"Could not fetch RainMaker nodes during setup: {e}")

    async_add_entities(switches, True)

class RainMakerSocket(SwitchEntity):
    def __init__(self, hass, base_url, device_data, node_detail, socket_device_name):
        self._hass = hass
        self._base_url = base_url
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
        """Send command to ESP RainMaker device."""
        try:
            session = async_get_clientsession(self._hass)
            # The payload uses the socket device name as the key
            payload = {self._socket_device_name: socket_data}

            async with session.post(
                f"{self._base_url}/setparams/{self._node_id}",
                json=payload,
                headers={"Content-Type": "application/json"}
            ) as resp:
                if resp.status == 200:
                    result = await resp.json()
                    if result.get("success", False):
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
                        _LOGGER.error(f"Failed to {action_description} {self._attr_name}: {result.get('error', 'Unknown error')}")
                else:
                    _LOGGER.error(f"HTTP error during {action_description} {self._attr_name}: {resp.status}")
        except Exception as e:
            _LOGGER.error(f"Error during {action_description} {self._attr_name}: {e}")

    async def async_update(self):
        """Update the socket state using efficient getparams endpoint."""
        try:
            session = async_get_clientsession(self._hass)
            # Use the more efficient getparams endpoint instead of nodedetails
            async with session.get(f"{self._base_url}/getparams/{self._node_id}") as resp:
                if resp.status == 200:
                    data = await resp.json()
                    params = data.get("params", {})

                    if self._socket_device_name in params:
                        socket_params = params[self._socket_device_name]

                        # Update socket state
                        self._attr_is_on = socket_params.get("Power", False)

                        _LOGGER.debug(f"Updated {self._attr_name}: Power={self._attr_is_on}")
                    else:
                        _LOGGER.warning(f"No parameters found for socket {self._socket_device_name} in node {self._node_id}")

                else:
                    _LOGGER.error(f"Failed to fetch params for {self._node_id}: HTTP {resp.status}")
        except Exception as e:
            _LOGGER.error(f"Error updating RainMaker socket {self._node_id}/{self._socket_device_name}: {e}")
