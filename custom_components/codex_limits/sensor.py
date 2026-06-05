from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

import aiohttp
import async_timeout

from homeassistant.components.sensor import SensorEntity, SensorStateClass
from homeassistant.const import PERCENTAGE
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.typing import ConfigType, DiscoveryInfoType
from homeassistant.components.notify import async_call as notify_call

from .const import (
    DOMAIN,
    ATTR_LIMIT_REACHED,
    ATTR_RESET_AT,
    ATTR_RESET_IN,
    ATTR_WINDOW_SECONDS,
    ATTR_PREVIOUS_VALUE,
    ATTR_PLAN_TYPE,
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
    session_token = domain_config.get("session_token")
    device_id = domain_config.get("device_id")
    cookie = domain_config.get("cookie")

    coordinator = CodexLimitsCoordinator(
        hass, api_url, scan_interval, session_token, device_id, cookie
    )

    sensors = [
        CodexLimitSensor(
            coordinator=coordinator,
            limit_key="primary_window",
            limit_name="Limit 5-godzinny",
            icon="mdi:timer-sand",
        ),
        CodexLimitSensor(
            coordinator=coordinator,
            limit_key="secondary_window",
            limit_name="Limit 5-dniowy",
            icon="mdi:calendar-clock",
        ),
    ]

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
        session_token: str | None,
        device_id: str | None,
        cookie: str | None,
    ):
        self.hass = hass
        self.api_url = api_url
        self.scan_interval = scan_interval
        self.session_token = session_token
        self.device_id = device_id
        self.cookie = cookie
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
        headers = {
            "Accept": "application/json",
            "User-Agent": "HomeAssistant/CodexLimits",
            "Referer": "https://chatgpt.com/codex/cloud/settings/analytics",
        }

        if self.session_token:
            token = self.session_token.removeprefix("Bearer ")
            headers["Authorization"] = f"Bearer {token}"

        if self.device_id:
            headers["OAI-Device-Id"] = self.device_id

        headers["OAI-Language"] = "en-US"

        cookies = {}
        if self.cookie:
            for item in self.cookie.split("; "):
                if "=" in item:
                    key, _, val = item.partition("=")
                    cookies[key] = val

        async with aiohttp.ClientSession(cookies=cookies) as session:
            async with async_timeout.timeout(30):
                async with session.get(self.api_url, headers=headers) as resp:
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
            f"Limit {limit_name} zresetowany!\n"
            f"Poprzednio: {old_value}%\n"
            f"Teraz: {new_value}%"
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
        icon: str,
    ):
        self._coordinator = coordinator
        self._limit_key = limit_key
        self._attr_name = f"Codex {limit_name}"
        self._attr_unique_id = f"codex_limits_{limit_key}"
        self._attr_icon = icon
        self._attr_native_value = None
        self._attr_native_unit_of_measurement = PERCENTAGE
        self._previous_value: int | None = None
        self._attr_extra_state_attributes: dict = {}
        self.available = True

        coordinator.register_sensor(self)

    def process_update(self, data: dict) -> None:
        rate_limit = data.get("rate_limit")
        if not rate_limit:
            self.available = False
            self.async_write_ha_state()
            return

        window = rate_limit.get(self._limit_key)
        if not window:
            self.available = False
            self.async_write_ha_state()
            return

        try:
            new_value = int(window["used_percent"])
        except (KeyError, TypeError, ValueError):
            self.available = False
            self.async_write_ha_state()
            return

        self.available = True
        old_value = self._attr_native_value

        limit_reached = rate_limit.get("limit_reached", False)
        reset_at_ts = window.get("reset_at")
        reset_in_s = window.get("reset_after_seconds", 0)
        window_s = window.get("limit_window_seconds", 0)

        reset_at_str = None
        if reset_at_ts:
            reset_at_str = datetime.fromtimestamp(
                reset_at_ts, tz=timezone.utc
            ).isoformat()

        if limit_reached and reset_in_s > 0:
            hours = reset_in_s // 3600
            minutes = (reset_in_s % 3600) // 60
            reset_label = f"za {hours}h {minutes}m"
        else:
            reset_label = ""

        self._attr_extra_state_attributes = {
            ATTR_LIMIT_REACHED: limit_reached,
            ATTR_RESET_AT: reset_at_str,
            ATTR_RESET_IN: reset_in_s,
            ATTR_WINDOW_SECONDS: window_s,
            ATTR_PREVIOUS_VALUE: self._previous_value,
            "reset_label": reset_label,
            ATTR_PLAN_TYPE: data.get("plan_type", "unknown"),
            "rate_allowed": rate_limit.get("allowed", True),
        }

        if old_value is not None and self._is_reset(old_value, new_value):
            _LOGGER.info(
                "Limit %s reset detected: %s%% -> %s%%",
                self._limit_key,
                old_value,
                new_value,
            )
            self._previous_value = old_value
            self.hass.async_create_task(
                self._coordinator.send_notification(
                    self._attr_name, old_value, new_value, self._limit_key
                )
            )

        self._attr_native_value = new_value
        self.async_write_ha_state()

    def _is_reset(self, old_value: int, new_value: int) -> bool:
        if new_value >= old_value:
            return False
        if old_value >= 30 and new_value <= 10:
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
