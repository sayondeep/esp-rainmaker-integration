from homeassistant.components.sensor import SensorEntity
from homeassistant.helpers.entity import DeviceInfo
from .const import DOMAIN
import logging

_LOGGER = logging.getLogger(__name__)

async def async_setup_entry(hass, entry, async_add_entities):
    entry_data = hass.data[DOMAIN][entry.entry_id]
    ws_client = entry_data["ws_client"]

    # Create status sensors for RainMaker devices
    sensors = []

    # Try to get RainMaker nodes and create status sensors
    try:
        # Get devices list via WebSocket
        response = await ws_client.send_request("rainmakernodes")
        devices = response.get("devices", [])

        for device in devices:
            # Get the actual device name from Light.Name parameter
            node_id = device.get("node_id")
            device_name = device.get("name", f"RainMaker Device {node_id[:8]}")

            if node_id:
                # Try to get the actual device name from Light parameters
                try:
                    params_response = await ws_client.send_request("getparams", {"node_id": node_id})
                    params = params_response.get("params", {})
                    light_params = params.get("Light", {})
                    light_device_name = light_params.get("Name", "")

                    if light_device_name:
                        device_name = light_device_name
                        _LOGGER.debug(f"Using Light.Name '{device_name}' for sensor {node_id}")
                except Exception as e:
                    _LOGGER.debug(f"Could not fetch Light.Name for {node_id}: {e}")

            # Update device data with the correct name
            device_with_name = device.copy()
            device_with_name["name"] = device_name

            # Create a status entity for each device
            sensors.append(RainMakerStatusEntity(hass, ws_client, device_with_name))

        _LOGGER.info(f"Found {len(devices)} ESP RainMaker devices for status entities")
    except Exception as e:
        _LOGGER.warning(f"Could not fetch RainMaker nodes during setup: {e}")

    async_add_entities(sensors, True)

class RainMakerStatusEntity(SensorEntity):
    def __init__(self, hass, ws_client, device_data):
        self._hass = hass
        self._ws_client = ws_client
        self._device_data = device_data
        self._node_id = device_data["node_id"]

        self._device_name = device_data.get("name", f"RainMaker Device {self._node_id[:8]}")
        self._device_type = device_data.get("type", "RainMaker Device")
        self._node_type = device_data.get("node_type", "unknown")

        self._attr_name = f"{self._device_name} Status"
        self._attr_unique_id = f"esp_rainmaker_status_{self._node_id}"

        # Get connectivity status
        connected = device_data.get("connected", False)
        self._attr_native_value = "online" if connected else "offline"
        self._attr_icon = "mdi:wifi"

    def _update_device_name_from_light_params(self, node_id):
        """Try to update device name from Light.Name parameter."""
        try:
            import asyncio
            loop = asyncio.get_event_loop()
            if loop.is_running():
                # If we're in an async context, we can't make sync calls
                return

            # This is a fallback - in practice, the name should be set correctly during setup
            _LOGGER.debug(f"Device name update requested for {node_id}, but async context prevents sync call")
        except Exception as e:
            _LOGGER.debug(f"Could not update device name for {node_id}: {e}")

    @property
    def name(self):
        """Return the name of the sensor entity."""
        return self._attr_name

    @property
    def device_info(self):
        return DeviceInfo(
            identifiers={(DOMAIN, f"rainmaker_{self._node_id}")},
            name=self._device_name,  # This should match the light entity device name
            manufacturer="Espressif",
            model=self._device_type,
            sw_version=self._node_type,
        )

    @property
    def extra_state_attributes(self):
        """Return additional attributes."""
        return {
            "node_id": self._node_id,
            "device_type": self._device_type,
            "node_type": self._node_type,
            "is_matter": self._device_data.get("is_matter", False),
            "connected": self._device_data.get("connected", False),
        }

    async def async_update(self):
        try:
            response = await self._ws_client.send_request("rainmakernodes")
            devices = response.get("devices", [])

            # Find this device in the updated data
            for device in devices:
                if device["node_id"] == self._node_id:
                    connected = device.get("connected", False)
                    self._attr_native_value = "online" if connected else "offline"

                    # Update device data for attributes
                    self._device_data = device
                    return

            # Device not found in updated data
            self._attr_native_value = "offline"
        except Exception as e:
            _LOGGER.error(f"Error updating RainMaker status entity {self._node_id}: {e}")
            self._attr_native_value = "unknown"
