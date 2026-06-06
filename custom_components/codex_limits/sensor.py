from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

import aiohttp
import async_timeout

from homeassistant.config_entries import ConfigEntry
from homeassistant.components.sensor import SensorEntity, SensorStateClass
from homeassistant.const import PERCENTAGE
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import (
    DataUpdateCoordinator,
    CoordinatorEntity,
    UpdateFailed,
)

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


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    _LOGGER.info("Setting up Codex Limits sensors")
    domain_config = hass.data.get(DOMAIN, {})
    api_url = domain_config.get("api_url") or DEFAULT_API_URL
    scan_interval = domain_config.get("scan_interval") or DEFAULT_SCAN_INTERVAL
    session_token = domain_config.get("session_token")
    device_id = domain_config.get("device_id")
    cookie = domain_config.get("cookie")

    coordinator = CodexLimitsCoordinator(
        hass, api_url, scan_interval, session_token, device_id, cookie
    )

    # Perform first refresh immediately so sensors have data on startup
    await coordinator.async_config_entry_first_refresh()

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

    async_add_entities(sensors)


class CodexLimitsCoordinator(DataUpdateCoordinator[dict]):
    def __init__(
        self,
        hass: HomeAssistant,
        api_url: str,
        scan_interval: int,
        session_token: str | None,
        device_id: str | None,
        cookie: str | None,
    ):
        self.api_url = api_url
        self.session_token = session_token
        self.device_id = device_id
        self.cookie = cookie
        self._last_notification: dict[str, datetime] = {}

        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=scan_interval),
        )

    async def _async_update_data(self) -> dict:
        _LOGGER.debug("Fetching limits from %s", self.api_url)
        try:
            return await self._fetch_limits()
        except Exception as e:
            _LOGGER.exception("Failed to fetch limits from Codex API: %s", e)
            raise UpdateFailed(f"Error fetching limits: {e}") from e

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

        await self.hass.services.async_call(
            "notify",
            "notify",
            {"message": message, "title": "Codex Limits"},
        )


class CodexLimitSensor(CoordinatorEntity[CodexLimitsCoordinator], SensorEntity):
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: CodexLimitsCoordinator,
        limit_key: str,
        limit_name: str,
        icon: str,
    ):
        super().__init__(coordinator)
        self._limit_key = limit_key
        self._attr_name = f"{limit_name}"
        self._attr_unique_id = f"codex_limits_{limit_key}"
        self._attr_icon = icon
        self._previous_value: int | None = None
        self._attr_native_value = None

    @property
    def available(self) -> bool:
        if not self.coordinator.last_update_success:
            return False
        if not self.coordinator.data:
            return False
        rate_limit = self.coordinator.data.get("rate_limit")
        if not rate_limit:
            return False
        return self._limit_key in rate_limit

    @property
    def native_value(self) -> int | None:
        if not self.coordinator.data:
            return None
        
        rate_limit = self.coordinator.data.get("rate_limit")
        if not rate_limit:
            return None
        
        window = rate_limit.get(self._limit_key)
        if not window:
            return None
        
        try:
            return int(window["used_percent"])
        except (KeyError, TypeError, ValueError):
            return None

    @property
    def extra_state_attributes(self) -> dict:
        if not self.coordinator.data:
            return {}

        rate_limit = self.coordinator.data.get("rate_limit", {})
        window = rate_limit.get(self._limit_key, {})

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

        return {
            ATTR_LIMIT_REACHED: limit_reached,
            ATTR_RESET_AT: reset_at_str,
            ATTR_RESET_IN: reset_in_s,
            ATTR_WINDOW_SECONDS: window_s,
            ATTR_PREVIOUS_VALUE: self._previous_value,
            "reset_label": reset_label,
            ATTR_PLAN_TYPE: self.coordinator.data.get("plan_type", "unknown"),
            "rate_allowed": rate_limit.get("allowed", True),
        }

    @property
    def native_unit_of_measurement(self) -> str:
        return PERCENTAGE

    def _handle_coordinator_update(self) -> None:
        old_value = self._attr_native_value
        new_value = self.native_value

        if old_value is not None and new_value is not None:
            if self._is_reset(old_value, new_value):
                _LOGGER.info(
                    "Limit %s reset detected: %s%% -> %s%%",
                    self._limit_key,
                    old_value,
                    new_value,
                )
                self._previous_value = old_value
                self.hass.async_create_task(
                    self.coordinator.send_notification(
                        self._attr_name, old_value, new_value, self._limit_key
                    )
                )

        self._attr_native_value = new_value
        super()._handle_coordinator_update()

    def _is_reset(self, old_value: int, new_value: int) -> bool:
        if new_value >= old_value:
            return False
        if old_value >= 30 and new_value <= 10:
            return True
        return False
