from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers import device_registry as dr
from .const import DOMAIN
import logging

_LOGGER = logging.getLogger(__name__)

PLATFORMS = ["sensor", "light"]

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = {
        "host": entry.data["host"],
        "port": entry.data["port"],
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
        hass.data[DOMAIN].pop(entry.entry_id)

        # Remove the service
        hass.services.async_remove(DOMAIN, "refresh_device_names")
    return unload_ok
