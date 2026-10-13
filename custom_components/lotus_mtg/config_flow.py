"""Config flow for Lotus MTG Life Counter."""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult

from .client import normalize_code
from .const import CONF_CODE, DOMAIN


class LotusConfigFlow(ConfigFlow, domain=DOMAIN):
    """Set up Lotus. The game code is optional and can be changed any time."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        await self.async_set_unique_id(DOMAIN)
        self._abort_if_unique_id_configured()
        errors: dict[str, str] = {}
        if user_input is not None:
            raw = user_input.get(CONF_CODE, "").strip()
            code = normalize_code(raw) if raw else None
            if raw and not code:
                errors[CONF_CODE] = "invalid_code"
            else:
                return self.async_create_entry(
                    title="Lotus MTG", data={CONF_CODE: code}
                )
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({vol.Optional(CONF_CODE, default=""): str}),
            errors=errors,
        )
