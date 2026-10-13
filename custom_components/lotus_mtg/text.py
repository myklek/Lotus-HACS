"""Game code text entity: type the new code here before each game."""

from __future__ import annotations

from homeassistant.components.text import TextEntity, TextMode
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import LotusConfigEntry
from .coordinator import LotusCoordinator
from .entity import LotusEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: LotusConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    async_add_entities([GameCode(entry.runtime_data)])


class GameCode(LotusEntity, TextEntity):
    _attr_translation_key = "game_code"
    _attr_icon = "mdi:cast"
    _attr_mode = TextMode.TEXT
    _attr_native_min = 0
    _attr_native_max = 100

    def __init__(self, coordinator: LotusCoordinator) -> None:
        super().__init__(coordinator, "game_code")

    @property
    def native_value(self) -> str:
        return self.coordinator.data.code or ""

    @property
    def extra_state_attributes(self) -> dict:
        return {"watch_url": self.coordinator.data.watch_url}

    async def async_set_value(self, value: str) -> None:
        try:
            await self.coordinator.async_set_code(value.strip())
        except ValueError as err:
            raise ServiceValidationError(
                "Game codes are 5 letters and numbers, like K7QX2 "
                "(a lifecounter.app/watch/... link works too)"
            ) from err
