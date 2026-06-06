from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.typing import ConfigType

from .const import DOMAIN, SERVICE_CHECK_LIMITS

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [Platform.SENSOR]

CONFIG_SCHEMA = vol.Schema(
    {
        DOMAIN: vol.Schema(
            {
                vol.Optional("api_url"): cv.url,
                vol.Optional("scan_interval", default=120): cv.positive_int,
                vol.Optional("session_token"): cv.string,
                vol.Optional("device_id"): cv.string,
                vol.Optional("cookie"): cv.string,
            }
        )
    },
    extra=vol.ALLOW_EXTRA,
)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    if DOMAIN not in config:
        return True

    conf = config[DOMAIN]
    data: dict[str, Any] = {
        "api_url": conf.get("api_url"),
        "scan_interval": conf.get("scan_interval", 120),
        "session_token": conf.get("session_token"),
        "device_id": conf.get("device_id"),
        "cookie": conf.get("cookie"),
    }

    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN].update(data)

    try:
        await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": "import"}, data=data
        )
    except Exception as e:
        _LOGGER.error("Failed to import codex_limits config entry: %s", e)

    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    _LOGGER.info("Setting up Codex Limits entry %s", entry.entry_id)

    hass.data.setdefault(DOMAIN, {})
    if not hass.data[DOMAIN]:
        hass.data[DOMAIN].update(entry.data)

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    async def handle_check_limits(call: ServiceCall) -> None:
        for component in hass.data.get("codex_limits_components", []):
            await component.async_update()

    hass.services.async_register(DOMAIN, SERVICE_CHECK_LIMITS, handle_check_limits)

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    hass.data[DOMAIN] = {}
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
