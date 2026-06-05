from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

import aiohttp
import async_timeout

from homeassistant.components.sensor import (
    SensorEntity,
    SensorStateClass,
)
from homeassistant.const import PERCENTAGE
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.typing import ConfigType, DiscoveryInfoType
from homeassistant.components.notify import async_call as notify_call

from .const import (
    DOMAIN,
    ATTR_LIMIT_NAME,
    ATTR_LIMIT_TYPE,
    ATTR_RESET_TIME,
    ATTR_PREVIOUS_VALUE,
    ATTR_IS_RESET,
    LIMIT_TYPES,
    DEFAULT_SCAN_INTERVAL,
    DEFAULT_API_URL,
)

_LOGGER = logging.getLogger(__name__)

NOTIFICATION_THROTTLE = timedelta(minutes=10)


async def async_setup_platform(
    hass: HomeAssistant,
    config: ConfigType,
    async_add_entities: AddEntitiesCallback,
    discovery_info: DiscoveryInfoType | None = None,
) -> None:
    domain_config = hass.data.get(DOMAIN, {})
    api_url = domain_config.get("api_url", DEFAULT_API_URL)
    scan_interval = domain_config.get("scan_interval", DEFAULT_SCAN_INTERVAL)
    api_key = domain_config.get("api_key")
    headers = domain_config.get("headers", {})
    limits_config = domain_config.get("limits_config", {})

    coordinator = CodexLimitsCoordinator(
        hass, api_url, scan_interval, api_key, headers
    )

    sensors = []
    for limit_key, limit_def in LIMIT_TYPES.items():
        custom_config = limits_config.get(limit_key, {})
        sensor = CodexLimitSensor(
            coordinator=coordinator,
            limit_key=limit_key,
            limit_name=custom_config.get("name", limit_def["name"]),
            limit_max=custom_config.get("max", limit_def["max"]),
            icon=custom_config.get("icon", limit_def["icon"]),
        )
        sensors.append(sensor)

    if not hass.data.get("codex_limits_components"):
        hass.data["codex_limits_components"] = []
    hass.data["codex_limits_components"].append(coordinator)

    async_add_entities(sensors, True)


class CodexLimitsCoordinator:
    def __init__(
        self,
        hass: HomeAssistant,
        api_url: str,
        scan_interval: int,
        api_key: str | None,
        headers: dict,
    ):
        self.hass = hass
        self.api_url = api_url
        self.scan_interval = scan_interval
        self.api_key = api_key
        self.headers = headers or {}
        self._sensors: list[CodexLimitSensor] = []
        self._last_notification: dict[str, datetime] = {}

    def register_sensor(self, sensor: CodexLimitSensor) -> None:
        self._sensors.append(sensor)

    async def async_update(self, *_) -> None:
        _LOGGER.debug("Fetching limits from %s", self.api_url)
        try:
            data = await self._fetch_limits()
        except Exception as e:
            _LOGGER.warning("Failed to fetch limits: %s", e)
            for sensor in self._sensors:
                if sensor.available:
                    sensor.available = False
                    sensor.async_write_ha_state()
            return

        for sensor in self._sensors:
            sensor.process_update(data)

    async def _fetch_limits(self) -> dict:
        req_headers = self.headers.copy()
        if self.api_key:
            req_headers["Authorization"] = f"Bearer {self.api_key}"
        req_headers.setdefault("Accept", "application/json")

        async with aiohttp.ClientSession() as session:
            async with async_timeout.timeout(30):
                async with session.get(self.api_url, headers=req_headers) as resp:
                    resp.raise_for_status()
                    return await resp.json()

    def can_notify(self, limit_key: str) -> bool:
        now = datetime.now(timezone.utc)
        last = self._last_notification.get(limit_key)
        if last is None or (now - last) > NOTIFICATION_THROTTLE:
            self._last_notification[limit_key] = now
            return True
        return False

    async def send_notification(
        self, limit_name: str, old_value, new_value, limit_key: str
    ) -> None:
        if not self.can_notify(limit_key):
            return

        message = (
            f"🔄 Limit {limit_name} został zresetowany!\n"
            f"Poprzednia wartość: {old_value}\n"
            f"Nowa wartość: {new_value}"
        )
        _LOGGER.info("Sending notification: %s", message)

        await notify_call(
            self.hass,
            service="notify",
            data={"message": message, "title": "Codex Limits"},
        )


class CodexLimitSensor(SensorEntity):
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_should_poll = False

    def __init__(
        self,
        coordinator: CodexLimitsCoordinator,
        limit_key: str,
        limit_name: str,
        limit_max: float,
        icon: str,
    ):
        self._coordinator = coordinator
        self._limit_key = limit_key
        self._limit_name = limit_name
        self._limit_max = limit_max
        self._attr_icon = icon
        self._attr_name = f"Codex {limit_name}"
        self._attr_unique_id = f"codex_limits_{limit_key}"
        self._attr_native_value = None
        self._attr_native_unit_of_measurement = PERCENTAGE
        self._previous_value = None
        self.available = True

        coordinator.register_sensor(self)

    @property
    def extra_state_attributes(self) -> dict:
        return {
            ATTR_LIMIT_NAME: self._limit_name,
            ATTR_LIMIT_TYPE: self._limit_key,
            ATTR_PREVIOUS_VALUE: self._previous_value,
        }

    def process_update(self, data: dict) -> None:
        raw_value = data.get(self._limit_key, data.get("limits", {}).get(self._limit_key))

        if raw_value is None:
            self.available = False
            self.async_write_ha_state()
            return

        try:
            new_value = float(raw_value)
        except (TypeError, ValueError):
            self.available = False
            self.async_write_ha_state()
            return

        self.available = True
        old_value = self._attr_native_value

        if old_value is not None and self._is_reset(old_value, new_value):
            _LOGGER.info(
                "Limit %s reset detected: %s -> %s",
                self._limit_key, old_value, new_value,
            )
            self._previous_value = old_value
            self.hass.async_create_task(
                self._coordinator.send_notification(
                    self._limit_name, old_value, new_value, self._limit_key
                )
            )

        self._attr_native_value = new_value
        self.async_write_ha_state()

    def _is_reset(self, old_value: float, new_value: float) -> bool:
        if new_value > old_value:
            if old_value < self._limit_max * 0.3 and new_value > self._limit_max * 0.7:
                return True
            if new_value >= self._limit_max:
                return True
        return False

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(
            async_track_time_interval(
                self.hass,
                self._coordinator.async_update,
                timedelta(seconds=self._coordinator.scan_interval),
            )
        )
