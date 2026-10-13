"""Binary sensors for Lotus MTG."""

from __future__ import annotations

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import LotusConfigEntry
from .coordinator import LotusCoordinator
from .entity import LotusEntity, LotusPlayerEntity


def player_entities(coordinator: LotusCoordinator, slots: range) -> list:
    return [
        cls(coordinator, slot) for slot in slots for cls in (PlayerTurn, PlayerAlive)
    ]


async def async_setup_entry(
    hass: HomeAssistant,
    entry: LotusConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    async_add_entities(
        [Connected(coordinator), HostOnline(coordinator)]
        + player_entities(coordinator, range(1, coordinator.slots + 1))
    )

    @callback
    def add_slots(old: int, new: int) -> None:
        async_add_entities(player_entities(coordinator, range(old + 1, new + 1)))

    coordinator.slot_listeners.append(add_slots)
    entry.async_on_unload(lambda: coordinator.slot_listeners.remove(add_slots))


class Connected(LotusEntity, BinarySensorEntity):
    _attr_translation_key = "connected"
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: LotusCoordinator) -> None:
        super().__init__(coordinator, "connected")

    @property
    def is_on(self) -> bool:
        return self.coordinator.data.connected


class HostOnline(LotusEntity, BinarySensorEntity):
    """Off when the host's phone stopped sending updates (asleep/offline)."""

    _attr_translation_key = "host_online"
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY

    def __init__(self, coordinator: LotusCoordinator) -> None:
        super().__init__(coordinator, "host_online")

    @property
    def is_on(self) -> bool:
        return self.coordinator.data.host_online


class PlayerTurn(LotusPlayerEntity, BinarySensorEntity):
    _attr_icon = "mdi:account-clock"

    def __init__(self, coordinator: LotusCoordinator, slot: int) -> None:
        super().__init__(coordinator, slot, "turn")
        self._attr_name = f"Player {slot} turn"

    @property
    def is_on(self) -> bool | None:
        return self.player.is_turn if self.player else None


class PlayerAlive(LotusPlayerEntity, BinarySensorEntity):
    _attr_icon = "mdi:heart-pulse"

    def __init__(self, coordinator: LotusCoordinator, slot: int) -> None:
        super().__init__(coordinator, slot, "alive")
        self._attr_name = f"Player {slot} alive"

    @property
    def is_on(self) -> bool | None:
        return self.player.alive if self.player else None
