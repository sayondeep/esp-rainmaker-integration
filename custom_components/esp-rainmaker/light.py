from homeassistant.components.light import (
    LightEntity,
    ColorMode,
    ATTR_BRIGHTNESS,
    ATTR_HS_COLOR,
)
from homeassistant.helpers.entity import DeviceInfo
from .const import DOMAIN
import logging
import colorsys

_LOGGER = logging.getLogger(__name__)

async def async_setup_entry(hass, entry, async_add_entities):
    entry_data = hass.data[DOMAIN][entry.entry_id]
    ws_client = entry_data["ws_client"]

    # Create light entities for RainMaker light devices
    lights = []

    # Try to get RainMaker nodes and create light entities
    try:
        # Get devices list via WebSocket
        response = await ws_client.send_request("rainmakernodes")
        devices = response.get("devices", [])

        for device in devices:
            # Check if this device has Light parameters (indicating it's a light device)
            node_id = device.get("node_id")
            if node_id:
                # Get detailed device info to check for light capabilities
                detail_response = await ws_client.send_request("nodedetails", {"node_id": node_id})
                node_details_data = detail_response.get("details", {})
                node_details = node_details_data.get("node_details", [])

                for node_detail in node_details:
                    # Check config.devices for light devices
                    config = node_detail.get("config", {})
                    devices_config = config.get("devices", [])

                    for device_config in devices_config:
                        device_type = device_config.get("type")
                        device_name = device_config.get("name", "Light")

                        # Check for any light device (lightbulb or dimmer switch)
                        is_light_device = (
                            device_type == "esp.device.lightbulb" or
                            (device_type == "esp.device.switch" and device_config.get("subtype") == "dimmer")
                        )

                        if is_light_device:
                            # Check if this device has Power parameter
                            has_power = any(
                                p.get("name") == "Power" and p.get("type") == "esp.param.power"
                                for p in device_config.get("params", [])
                            )

                            if has_power:
                                lights.append(RainMakerLight(hass, ws_client, device, node_detail, device_name, device_config))
                                _LOGGER.debug(f"Found light device: {device_name} in node {node_id}")

        _LOGGER.info(f"Found {len(lights)} ESP RainMaker lights")
    except Exception as e:
        _LOGGER.warning(f"Could not fetch RainMaker nodes during setup: {e}")

    async_add_entities(lights, True)

class RainMakerLight(LightEntity):
    def __init__(self, hass, ws_client, device_data, node_detail, device_name, device_config):
        self._hass = hass
        self._ws_client = ws_client
        self._device_data = device_data
        self._node_detail = node_detail
        self._node_id = device_data["node_id"]
        self._device_param_name = device_name  # Device name used as param key (e.g., "Light", "DMHCM")
        self._device_config = device_config

        # Use device name as param key
        self._param_key = device_name

        # Detect capabilities from device config
        params_config = device_config.get("params", [])
        self._has_hue = any(p.get("name") == "Hue" and p.get("type") == "esp.param.hue" for p in params_config)
        self._has_saturation = any(p.get("name") == "Saturation" and p.get("type") == "esp.param.saturation" for p in params_config)
        self._has_color = self._has_hue and self._has_saturation

        # Detect brightness parameter name (could be "Brightness" or "brightness")
        has_brightness_cap = any(p.get("name") == "Brightness" and p.get("type") == "esp.param.brightness" for p in params_config)
        has_brightness_lower = any(p.get("name") == "brightness" and p.get("type") == "esp.param.brightness" for p in params_config)
        self._brightness_param_name = "Brightness" if has_brightness_cap else ("brightness" if has_brightness_lower else "Brightness")

        # Get light parameters from node_detail
        params = node_detail.get("params", {})
        light_params = params.get(self._param_key, {})

        # Get device name from params or fallback
        device_name_from_params = light_params.get("Name", "")
        fallback_name = node_detail.get("name", device_data.get("name", f"RainMaker Light {self._node_id[:8]}"))
        self._device_name = device_name_from_params if device_name_from_params else fallback_name

        self._model = node_detail.get("model", "Unknown")
        self._fw_version = node_detail.get("fw_version", "Unknown")

        self._attr_name = self._device_name
        unique_id_suffix = f"_{device_name}" if device_name != "Light" else ""
        self._attr_unique_id = f"esp_rainmaker_light_{self._node_id}{unique_id_suffix}"

        # Set initial state from parameters
        self._attr_is_on = light_params.get("Power", False)
        self._brightness = light_params.get(self._brightness_param_name, 100)
        self._hue = light_params.get("Hue", 0) if self._has_hue else 0
        self._saturation = light_params.get("Saturation", 100) if self._has_saturation else 100

        # Set supported color modes based on capabilities
        if self._has_color:
            self._attr_supported_color_modes = {ColorMode.HS}
            self._attr_color_mode = ColorMode.HS
        else:
            self._attr_supported_color_modes = {ColorMode.BRIGHTNESS}
            self._attr_color_mode = ColorMode.BRIGHTNESS

        # Set faster polling for more responsive updates (10 seconds instead of 30)
        from datetime import timedelta
        self._attr_scan_interval = timedelta(seconds=10)

    @property
    def name(self):
        """Return the name of the light entity."""
        return self._attr_name

    @property
    def device_info(self):
        return DeviceInfo(
            identifiers={(DOMAIN, f"rainmaker_{self._node_id}")},
            name=self._device_name,  # This will update when _device_name changes
            manufacturer="Espressif",
            model=self._model,
            sw_version=self._fw_version,
        )

    @property
    def brightness(self):
        """Return the brightness of this light between 0..255."""
        return int(self._brightness * 255 / 100)

    @property
    def hs_color(self):
        """Return the hue and saturation color value [float, float]."""
        return (self._hue, self._saturation)

    @property
    def extra_state_attributes(self):
        """Return additional attributes."""
        attrs = {
            "node_id": self._node_id,
            "device_type": "rainmaker_light",
            "model": self._model,
            "firmware_version": self._fw_version,
            "raw_brightness": self._brightness,
        }
        if self._has_color:
            attrs["raw_hue"] = self._hue
            attrs["raw_saturation"] = self._saturation
        return attrs

    async def async_turn_on(self, **kwargs):
        """Turn on the light."""
        # Extract parameters from kwargs
        brightness = kwargs.get(ATTR_BRIGHTNESS)
        hs_color = kwargs.get(ATTR_HS_COLOR)

        # Prepare data to send to device
        light_data = {"Power": True}

        # Convert brightness from 0-255 to 0-100
        if brightness is not None:
            brightness_pct = int(brightness * 100 / 255)
            light_data[self._brightness_param_name] = brightness_pct
            self._brightness = brightness_pct
        else:
            # If no brightness specified, use current brightness or default to 100
            light_data[self._brightness_param_name] = self._brightness if self._brightness > 0 else 100

        # Convert HS color (only if device supports it)
        if self._has_color and hs_color is not None:
            hue, saturation = hs_color
            light_data["Hue"] = int(hue)
            light_data["Saturation"] = int(saturation)
            self._hue = int(hue)
            self._saturation = int(saturation)

        # Send command to ESP RainMaker device
        await self._send_command(light_data, "turn on")

    async def async_turn_off(self, **kwargs):
        """Turn off the light."""
        light_data = {"Power": False}
        await self._send_command(light_data, "turn off")

    async def _send_command(self, light_data, action_description):
        """Send command to ESP RainMaker device via WebSocket."""
        try:
            # Use correct param key: "Light" for regular lights, device name for dimmers
            payload = {self._param_key: light_data}

            response = await self._ws_client.send_request(
                "setparams",
                {"node_id": self._node_id, "data": payload}
            )

            # Check if device is offline
            if response.get("offline", False):
                _LOGGER.warning(f"Cannot {action_description} {self._device_name}: device is offline")
                return

            if response.get("success", False):
                # Update local state immediately for instant UI feedback
                if "Power" in light_data:
                    self._attr_is_on = light_data["Power"]
                if self._brightness_param_name in light_data:
                    self._brightness = light_data[self._brightness_param_name]
                if "Hue" in light_data:
                    self._hue = light_data["Hue"]
                if "Saturation" in light_data:
                    self._saturation = light_data["Saturation"]

                # Create detailed log message
                params_str = ", ".join([f"{k}={v}" for k, v in light_data.items()])
                _LOGGER.info(f"Successfully {action_description} {self._device_name}: {params_str}")

                # Immediate state update for UI responsiveness
                self.async_write_ha_state()

                # Schedule a delayed refresh to get actual device state (2 seconds later)
                self._hass.loop.call_later(2.0, lambda: self._hass.async_create_task(self.async_update()))

            else:
                error_msg = response.get('error', 'Unknown error')
                # Don't log errors for offline devices - they're expected
                if "offline" not in error_msg.lower():
                    _LOGGER.error(f"Failed to {action_description} {self._device_name}: {error_msg}")
        except Exception as e:
            error_msg = str(e).lower()
            # Don't log errors for offline devices - they're expected
            if "offline" not in error_msg and "could not connect" not in error_msg:
                _LOGGER.error(f"Error during {action_description} {self._device_name}: {e}")

    async def async_set_brightness(self, brightness_pct):
        """Set brightness without changing power state (custom method)."""
        if not self._attr_is_on:
            _LOGGER.warning(f"Cannot set brightness on {self._device_name}: light is off")
            return

        light_data = {self._brightness_param_name: brightness_pct}
        await self._send_command(light_data, f"set brightness to {brightness_pct}%")

    async def async_set_hs_color(self, hue, saturation):
        """Set hue and saturation without changing power state."""
        if not self._has_color:
            _LOGGER.warning(f"Cannot set color on {self._device_name}: device does not support color")
            return
        if not self._attr_is_on:
            _LOGGER.warning(f"Cannot set color on {self._device_name}: light is off")
            return

        # Validate ranges
        hue = max(0, min(360, int(hue)))
        saturation = max(0, min(100, int(saturation)))

        light_data = {"Hue": hue, "Saturation": saturation}
        await self._send_command(light_data, f"set color to H:{hue}° S:{saturation}%")

    async def async_set_hue(self, hue):
        """Set hue only without changing power state or saturation."""
        if not self._has_color:
            _LOGGER.warning(f"Cannot set hue on {self._device_name}: device does not support color")
            return
        if not self._attr_is_on:
            _LOGGER.warning(f"Cannot set hue on {self._device_name}: light is off")
            return

        hue = max(0, min(360, int(hue)))
        light_data = {"Hue": hue}
        await self._send_command(light_data, f"set hue to {hue}°")

    async def async_set_saturation(self, saturation):
        """Set saturation only without changing power state or hue."""
        if not self._has_color:
            _LOGGER.warning(f"Cannot set saturation on {self._device_name}: device does not support color")
            return
        if not self._attr_is_on:
            _LOGGER.warning(f"Cannot set saturation on {self._device_name}: light is off")
            return

        saturation = max(0, min(100, int(saturation)))
        light_data = {"Saturation": saturation}
        await self._send_command(light_data, f"set saturation to {saturation}%")

    async def async_set_full_color(self, brightness_pct=None, hue=None, saturation=None):
        """Set brightness, hue, and saturation in one command."""
        if not self._has_color and (hue is not None or saturation is not None):
            _LOGGER.warning(f"Cannot set color on {self._device_name}: device does not support color")
            return
        if not self._attr_is_on:
            _LOGGER.warning(f"Cannot set color on {self._device_name}: light is off")
            return

        light_data = {}
        params = []

        if brightness_pct is not None:
            brightness_pct = max(0, min(100, int(brightness_pct)))
            light_data[self._brightness_param_name] = brightness_pct
            params.append(f"brightness:{brightness_pct}%")

        if self._has_color:
            if hue is not None:
                hue = max(0, min(360, int(hue)))
                light_data["Hue"] = hue
                params.append(f"Hue:{hue}°")

            if saturation is not None:
                saturation = max(0, min(100, int(saturation)))
                light_data["Saturation"] = saturation
                params.append(f"Saturation:{saturation}%")

        if light_data:
            params_str = ", ".join(params)
            await self._send_command(light_data, f"set color parameters ({params_str})")

    async def async_force_refresh(self):
        """Force an immediate refresh of device state (for manual triggers)."""
        _LOGGER.info(f"Force refresh triggered for {self._device_name}")
        await self.async_update()
        self.async_write_ha_state()

    def _update_device_name(self, light_params):
        """Update device name from Name parameter and notify Home Assistant of the change."""
        device_name_from_params = light_params.get("Name", "")
        if device_name_from_params and device_name_from_params != self._device_name:
            old_name = self._device_name
            self._device_name = device_name_from_params
            self._attr_name = self._device_name
            _LOGGER.info(f"Device name updated from '{old_name}' to '{self._device_name}' for node {self._node_id}")

            # Update the device registry with the new name
            self._update_device_registry()

            # Force Home Assistant to update the entity name by marking it as changed
            self.async_write_ha_state()
        elif device_name_from_params and device_name_from_params == self._device_name:
            # Name is already correct, no change needed
            pass
        else:
            # No name in parameters, keep current name
            _LOGGER.debug(f"No Name parameter in {self._param_key} data for {self._node_id}, keeping current name: {self._device_name}")

    def _update_device_registry(self):
        """Update the device registry with the new device name."""
        try:
            from homeassistant.helpers import device_registry as dr

            device_registry = dr.async_get(self._hass)
            device_identifier = (DOMAIN, f"rainmaker_{self._node_id}")

            # Find the device in the registry
            device = device_registry.async_get_device(identifiers={device_identifier})

            if device:
                # Update the device name in the registry
                device_registry.async_update_device(
                    device.id,
                    name=self._device_name
                )
                _LOGGER.info(f"Updated device registry name to '{self._device_name}' for device {device.id}")
            else:
                _LOGGER.warning(f"Device not found in registry for {self._node_id}")

        except Exception as e:
            _LOGGER.error(f"Failed to update device registry for {self._node_id}: {e}")

    async def async_update(self):
        """Update the light state using efficient getparams endpoint via WebSocket."""
        try:
            response = await self._ws_client.send_request(
                "getparams",
                {"node_id": self._node_id}
            )

            # Check if device is offline
            if response.get("offline", False):
                _LOGGER.debug(f"Device {self._device_name} is offline, skipping update")
                return

            params = response.get("params", {})

            if self._param_key in params:
                light_params = params[self._param_key]

                # Update device name first (this may trigger HA state update)
                self._update_device_name(light_params)

                # Update light state
                self._attr_is_on = light_params.get("Power", False)
                self._brightness = light_params.get(self._brightness_param_name, 100)

                if self._has_color:
                    self._hue = light_params.get("Hue", 0)
                    self._saturation = light_params.get("Saturation", 100)
                    _LOGGER.debug(f"Updated {self._device_name}: Power={self._attr_is_on}, {self._brightness_param_name}={self._brightness}, Hue={self._hue}, Saturation={self._saturation}")
                else:
                    _LOGGER.debug(f"Updated {self._device_name}: Power={self._attr_is_on}, {self._brightness_param_name}={self._brightness}")
            else:
                _LOGGER.warning(f"No {self._param_key} parameters found for {self._node_id}")

        except Exception as e:
            error_msg = str(e).lower()
            # Don't log errors for offline devices - they're expected
            if "offline" not in error_msg and "could not connect" not in error_msg:
                _LOGGER.error(f"Error updating RainMaker light {self._node_id}: {e}")
