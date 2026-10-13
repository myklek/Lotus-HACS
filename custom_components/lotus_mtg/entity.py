"""Base entity for Lotus MTG."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import LotusCoordinator, Player


class LotusEntity(CoordinatorEntity[LotusCoordinator]):
    """Common device info."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: LotusCoordinator, key: str) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.entry.entry_id}_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, coordinator.entry.entry_id)},
            name="Lotus MTG",
            manufacturer="lifecounter.app",
            entry_type=DeviceEntryType.SERVICE,
            configuration_url="https://lifecounter.app",
        )


class LotusPlayerEntity(LotusEntity):
    """Entity bound to a seat slot (Player 1, Player 2, ...)."""

    def __init__(self, coordinator: LotusCoordinator, slot: int, key: str) -> None:
        super().__init__(coordinator, f"player_{slot}_{key}")
        self.slot = slot

    @property
    def player(self) -> Player | None:
        return self.coordinator.data.player(self.slot)

    @property
    def available(self) -> bool:
        return super().available and self.player is not None
