"""Sensors for Lotus MTG."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import LotusConfigEntry
from .const import (
    STATUS_CONNECTING,
    STATUS_DISCONNECTED,
    STATUS_ENDED,
    STATUS_IDLE,
    STATUS_LIVE,
    STATUS_NOT_FOUND,
)
from .coordinator import LotusCoordinator, LotusData, Player
from .entity import LotusEntity, LotusPlayerEntity


@dataclass(frozen=True, kw_only=True)
class GameSensorDescription(SensorEntityDescription):
    value_fn: Callable[[LotusData], Any]
    attrs_fn: Callable[[LotusData], dict[str, Any]] | None = None


@dataclass(frozen=True, kw_only=True)
class PlayerSensorDescription(SensorEntityDescription):
    value_fn: Callable[[Player], Any]
    name_suffix: str


@dataclass(frozen=True, kw_only=True)
class StatsSensorDescription(SensorEntityDescription):
    """Sensor over the saved games; both functions get the coordinator's stats."""

    value_fn: Callable[[dict[str, Any]], Any]
    attrs_fn: Callable[[dict[str, Any]], dict[str, Any]]


def _top(rows: list[dict[str, Any]], key: str, value: str) -> Any:
    return rows[0][value] if rows and rows[0][key] > 0 else None


def _game_attrs(d: LotusData) -> dict[str, Any]:
    game = d.game or {}
    meta = d.meta or {}
    clock = game.get("clock") if isinstance(game.get("clock"), dict) else {}
    return {
        "code": d.code,
        "watch_url": d.watch_url,
        "game_name": game.get("name"),
        "game_no": game.get("gameNo"),
        "modes": game.get("modes"),
        "day_night": game.get("dayNight"),
        "player_count": len(d.players),
        "players_alive": sum(1 for p in d.players if p.alive),
        "clock_started": clock.get("start"),
        "clock_paused": bool(clock.get("pausedAt")),
        "last_recap": (
            f"https://lifecounter.app/recap/{meta['lastRecapId']}"
            if meta.get("lastRecapId")
            else None
        ),
        "players": [
            {"slot": p.slot, "name": p.name, "life": p.life, "alive": p.alive}
            for p in d.players
        ],
    }


GAME_SENSORS: tuple[GameSensorDescription, ...] = (
    GameSensorDescription(
        key="status",
        translation_key="status",
        device_class=SensorDeviceClass.ENUM,
        options=[
            STATUS_IDLE,
            STATUS_CONNECTING,
            STATUS_LIVE,
            STATUS_ENDED,
            STATUS_NOT_FOUND,
            STATUS_DISCONNECTED,
        ],
        value_fn=lambda d: d.status,
        attrs_fn=_game_attrs,
    ),
    GameSensorDescription(
        key="turn",
        translation_key="turn",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda d: (d.turn or {}).get("number"),
    ),
    GameSensorDescription(
        key="active_player",
        translation_key="active_player",
        value_fn=lambda d: d.active_player.name if d.active_player else None,
        attrs_fn=lambda d: (
            {"slot": d.active_player.slot, **d.active_player.attributes()}
            if d.active_player
            else {}
        ),
    ),
    GameSensorDescription(
        key="winner",
        translation_key="winner",
        value_fn=lambda d: d.winner.name if d.winner else None,
        attrs_fn=lambda d: (
            {
                "slot": d.winner.slot,
                "commander": d.winner.commander,
                "image": d.winner.image,
                "game_no": (d.game or {}).get("gameNo"),
                "winner_text": (d.game or {}).get("winnerText"),
            }
            if d.winner
            else {}
        ),
    ),
    GameSensorDescription(
        key="players_alive",
        translation_key="players_alive",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda d: sum(1 for p in d.players if p.alive) if d.players else None,
    ),
)

PLAYER_SENSORS: tuple[PlayerSensorDescription, ...] = (
    PlayerSensorDescription(
        key="life",
        name_suffix="life",
        icon="mdi:heart",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda p: p.life,
    ),
    PlayerSensorDescription(
        key="name",
        name_suffix="name",
        icon="mdi:account",
        value_fn=lambda p: p.name,
    ),
    PlayerSensorDescription(
        key="commander",
        name_suffix="commander",
        icon="mdi:crown",
        value_fn=lambda p: p.commander,
    ),
    PlayerSensorDescription(
        key="poison",
        name_suffix="poison",
        icon="mdi:water",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda p: p.poison,
    ),
    PlayerSensorDescription(
        key="commander_damage",
        name_suffix="commander damage",
        icon="mdi:sword",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda p: p.commander_damage_max,
    ),
)


STATS_SENSORS: tuple[StatsSensorDescription, ...] = (
    StatsSensorDescription(
        key="games_recorded",
        translation_key="games_recorded",
        icon="mdi:counter",
        state_class=SensorStateClass.TOTAL,
        value_fn=lambda s: s["games"],
        attrs_fn=lambda s: {"unfinished": s["unfinished"], "last_game": s["last"]},
    ),
    StatsSensorDescription(
        key="most_wins",
        translation_key="most_wins",
        icon="mdi:trophy",
        value_fn=lambda s: _top(s["players"], "wins", "player"),
        attrs_fn=lambda s: {"leaderboard": s["players"]},
    ),
    StatsSensorDescription(
        key="top_commander",
        translation_key="top_commander",
        icon="mdi:crown",
        value_fn=lambda s: _top(s["commanders"], "wins", "commander"),
        attrs_fn=lambda s: {"commanders": s["commanders"]},
    ),
    StatsSensorDescription(
        key="top_attacker",
        translation_key="top_attacker",
        icon="mdi:sword-cross",
        value_fn=lambda s: _top(s["attackers"], "damage", "player"),
        attrs_fn=lambda s: {"attackers": s["attackers"], "pairs": s["pairs"]},
    ),
)


def player_entities(coordinator: LotusCoordinator, slots: range) -> list[SensorEntity]:
    return [
        LotusPlayerSensor(coordinator, slot, desc)
        for slot in slots
        for desc in PLAYER_SENSORS
    ]


async def async_setup_entry(
    hass: HomeAssistant,
    entry: LotusConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    entities: list[SensorEntity] = [
        LotusGameSensor(coordinator, desc) for desc in GAME_SENSORS
    ]
    entities += [LotusStatsSensor(coordinator, desc) for desc in STATS_SENSORS]
    entities += player_entities(coordinator, range(1, coordinator.slots + 1))
    async_add_entities(entities)

    @callback
    def add_slots(old: int, new: int) -> None:
        async_add_entities(player_entities(coordinator, range(old + 1, new + 1)))

    coordinator.slot_listeners.append(add_slots)
    entry.async_on_unload(lambda: coordinator.slot_listeners.remove(add_slots))


class LotusGameSensor(LotusEntity, SensorEntity):
    entity_description: GameSensorDescription

    def __init__(
        self, coordinator: LotusCoordinator, description: GameSensorDescription
    ) -> None:
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> Any:
        return self.entity_description.value_fn(self.coordinator.data)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        fn = self.entity_description.attrs_fn
        return fn(self.coordinator.data) if fn else None


class LotusStatsSensor(LotusEntity, SensorEntity):
    entity_description: StatsSensorDescription

    def __init__(
        self, coordinator: LotusCoordinator, description: StatsSensorDescription
    ) -> None:
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> Any:
        return self.entity_description.value_fn(self.coordinator.stats)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return self.entity_description.attrs_fn(self.coordinator.stats)


class LotusPlayerSensor(LotusPlayerEntity, SensorEntity):
    entity_description: PlayerSensorDescription

    def __init__(
        self,
        coordinator: LotusCoordinator,
        slot: int,
        description: PlayerSensorDescription,
    ) -> None:
        super().__init__(coordinator, slot, description.key)
        self.entity_description = description
        self._attr_name = f"Player {slot} {description.name_suffix}"

    @property
    def native_value(self) -> Any:
        player = self.player
        return self.entity_description.value_fn(player) if player else None

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        player = self.player
        if not player:
            return None
        if self.entity_description.key == "life":
            return player.attributes()
        if self.entity_description.key == "commander_damage":
            return {"player_name": player.name, "from": player.commander_damage}
        if self.entity_description.key == "commander":
            return {"player_name": player.name, "image": player.image}
        return {"player_name": player.name}
