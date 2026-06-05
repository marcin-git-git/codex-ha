from __future__ import annotations

import asyncio
import logging

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.const import CONF_API_KEY
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.typing import ConfigType

from .const import DOMAIN, PLATFORMS, SERVICE_CHECK_LIMITS

_LOGGER = logging.getLogger(__name__)

CONFIG_SCHEMA = vol.Schema(
    {
        DOMAIN: vol.Schema(
            {
                vol.Required("api_url"): cv.url,
                vol.Optional("scan_interval", default=60): cv.positive_int,
                vol.Optional("api_key"): cv.string,
                vol.Optional("headers", default={}): dict,
                vol.Optional("limits", default={}): dict,
            }
        )
    },
    extra=vol.ALLOW_EXTRA,
)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    if DOMAIN not in config:
        return True

    conf = config[DOMAIN]
    hass.data[DOMAIN] = {
        "api_url": conf["api_url"],
        "scan_interval": conf.get("scan_interval", 60),
        "api_key": conf.get("api_key"),
        "headers": conf.get("headers", {}),
        "limits_config": conf.get("limits", {}),
    }

    hass.helpers.discovery.load_platform("sensor", DOMAIN, {}, config)

    async def handle_check_limits(call: ServiceCall) -> None:
        _LOGGER.info("Manual check_limits service called")
        for component in hass.data.get("codex_limits_components", []):
            await component.async_update()

    hass.services.async_register(DOMAIN, SERVICE_CHECK_LIMITS, handle_check_limits)

    return True
