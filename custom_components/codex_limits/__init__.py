from __future__ import annotations

import logging

import voluptuous as vol

from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.typing import ConfigType

from .const import DOMAIN, PLATFORMS, SERVICE_CHECK_LIMITS

_LOGGER = logging.getLogger(__name__)

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
    hass.data[DOMAIN] = {
        "api_url": conf.get("api_url"),
        "scan_interval": conf.get("scan_interval", 120),
        "session_token": conf.get("session_token"),
        "device_id": conf.get("device_id"),
        "cookie": conf.get("cookie"),
    }

    hass.helpers.discovery.load_platform("sensor", DOMAIN, {}, config)

    async def handle_check_limits(call: ServiceCall) -> None:
        _LOGGER.info("Manual check_limits service called")
        for component in hass.data.get("codex_limits_components", []):
            await component.async_update()

    hass.services.async_register(DOMAIN, SERVICE_CHECK_LIMITS, handle_check_limits)

    return True
