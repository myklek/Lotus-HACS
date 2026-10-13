"""Lotus MTG Life Counter integration."""

from __future__ import annotations

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import ServiceValidationError
import homeassistant.helpers.config_validation as cv

from .const import DOMAIN, SERVICE_SET_GAME_CODE
from .coordinator import LotusCoordinator

PLATFORMS = [Platform.BINARY_SENSOR, Platform.SENSOR, Platform.TEXT]

type LotusConfigEntry = ConfigEntry[LotusCoordinator]

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

SET_CODE_SCHEMA = vol.Schema({vol.Optional("code", default=""): cv.string})


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    async def set_game_code(call: ServiceCall) -> None:
        entries = [
            e
            for e in hass.config_entries.async_entries(DOMAIN)
            if e.state is ConfigEntryState.LOADED
        ]
        if not entries:
            raise ServiceValidationError("Lotus MTG is not set up")
        try:
            for entry in entries:
                await entry.runtime_data.async_set_code(call.data["code"])
        except ValueError as err:
            raise ServiceValidationError(str(err)) from err

    hass.services.async_register(
        DOMAIN, SERVICE_SET_GAME_CODE, set_game_code, schema=SET_CODE_SCHEMA
    )
    return True


async def async_setup_entry(hass: HomeAssistant, entry: LotusConfigEntry) -> bool:
    coordinator = LotusCoordinator(hass, entry)
    entry.runtime_data = coordinator
    await coordinator.async_start()
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: LotusConfigEntry) -> bool:
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        await entry.runtime_data.async_shutdown()
    return unloaded
