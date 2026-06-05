from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigFlow
from homeassistant.data_entry_flow import FlowResult

from .const import DOMAIN


class CodexLimitsConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        return self.async_abort(reason="single_instance_allowed")

    async def async_step_import(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        existing = self._async_current_entries()
        if existing:
            return self.async_abort(reason="single_instance_allowed")

        return self.async_create_entry(
            title="Codex Limits Monitor",
            data=user_input or {},
        )
