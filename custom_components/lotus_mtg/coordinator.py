"""Coordinator holding the current Lotus game state."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import timedelta
import json
import logging
import time
from typing import Any

import aiohttp
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .client import (
    LotusGameStream,
    card_id_from_image,
    fetch_card_name,
    normalize_code,
)
from .const import (
    CONF_CODE,
    CONF_SLOTS,
    COUNTER_NAMES,
    DEFAULT_SLOTS,
    DOMAIN,
    EVENT_GAME_RECORDED,
    EVENT_GAME_WON,
    EVENT_LIFE_CHANGED,
    EVENT_NEW_GAME,
    EVENT_PLAYER_ELIMINATED,
    EVENT_TURN_CHANGED,
    GAMES_FILE,
    HOST_OFFLINE_AFTER,
    MAX_SLOTS,
    PLAYER_COLORS,
    STATUS_CONNECTING,
    STATUS_DISCONNECTED,
    STATUS_ENDED,
    STATUS_IDLE,
    STATUS_LIVE,
    STATUS_NOT_FOUND,
    WATCH_URL,
)
from .gamelog import GameLog, read_records, summarize, write_record

_LOGGER = logging.getLogger(__name__)


@dataclass
class Player:
    """One player of the current game."""

    slot: int
    id: str
    name: str
    life: int | None
    alive: bool
    color: str | None
    image: str | None
    commander: str | None
    counters: dict[str, Any]
    commander_damage: dict[str, int]
    commander_damage_max: int
    is_turn: bool
    is_winner: bool
    is_monarch: bool
    has_initiative: bool

    @property
    def poison(self) -> int:
        try:
            return int(self.counters.get("poison") or 0)
        except (TypeError, ValueError):
            return 0

    @property
    def rgb_color(self) -> tuple[int, int, int] | None:
        color = self.color or ""
        if color.startswith("#") and len(color) == 7:
            try:
                return tuple(int(color[i : i + 2], 16) for i in (1, 3, 5))  # type: ignore[return-value]
            except ValueError:
                return None
        return PLAYER_COLORS.get(color)

    def attributes(self) -> dict[str, Any]:
        return {
            "player_id": self.id,
            "player_name": self.name,
            "alive": self.alive,
            "color": self.color,
            "rgb_color": list(self.rgb_color) if self.rgb_color else None,
            "commander": self.commander,
            "image": self.image,
            "is_turn": self.is_turn,
            "is_winner": self.is_winner,
            "monarch": self.is_monarch,
            "initiative": self.has_initiative,
            "poison": self.poison,
            "commander_damage": self.commander_damage,
            "commander_damage_max": self.commander_damage_max,
            "counters": {
                COUNTER_NAMES.get(k, k): v for k, v in self.counters.items()
            },
        }


@dataclass
class LotusData:
    """Snapshot handed to entities."""

    code: str | None = None
    status: str = STATUS_IDLE
    connected: bool = False
    host_online: bool = False
    meta: dict[str, Any] | None = None
    game: dict[str, Any] | None = None
    players: list[Player] = field(default_factory=list)

    @property
    def watch_url(self) -> str | None:
        return f"{WATCH_URL}{self.code}" if self.code else None

    def player(self, slot: int) -> Player | None:
        return self.players[slot - 1] if 0 < slot <= len(self.players) else None

    @property
    def turn(self) -> dict[str, Any] | None:
        turn = (self.game or {}).get("turn")
        return turn if isinstance(turn, dict) else None

    @property
    def active_player(self) -> Player | None:
        return next((p for p in self.players if p.is_turn), None)

    @property
    def winner(self) -> Player | None:
        return next((p for p in self.players if p.is_winner), None)


def _ordered_players(game: dict[str, Any]) -> list[dict[str, Any]]:
    """Stable seat order: layout (top-left first), then seat list, then raw order."""
    players = [p for p in game.get("players") or [] if isinstance(p, dict)]
    by_id = {p.get("id"): p for p in players}
    layout = game.get("layout")
    order: list[Any]
    if isinstance(layout, dict) and all(
        isinstance(layout.get(p.get("id")), list) and len(layout[p.get("id")]) == 4
        for p in players
    ):
        rects = [(p.get("id"), layout[p.get("id")]) for p in players]
        rects.sort(key=lambda r: (round(r[1][1] / 0.02), r[1][0]))
        order = [r[0] for r in rects]
    else:
        order = list(game.get("seat") or [p.get("id") for p in players])
    seen: list[Any] = []
    for pid in order + [p.get("id") for p in players]:
        if pid in by_id and pid not in seen:
            seen.append(pid)
    return [by_id[pid] for pid in seen]


def _to_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def parse_players(
    game: dict[str, Any], commanders: dict[str, str | None] | None = None
) -> list[Player]:
    """Build players; commanders maps Scryfall card ids to already resolved names."""
    raw = _ordered_players(game)
    names = {p.get("id"): str(p.get("nick") or p.get("id")) for p in raw}
    turn = game.get("turn") if isinstance(game.get("turn"), dict) else {}
    result = []
    for slot, p in enumerate(raw, start=1):
        cmd: dict[str, int] = {}
        cmd_max = 0
        for source, dmg in (p.get("cmd") or {}).items():
            if source not in names:
                continue
            parts = dmg if isinstance(dmg, list) else [dmg]
            values = [_to_int(v) for v in parts[:2]]
            cmd[names[source]] = sum(values)
            cmd_max = max([cmd_max, *values])
        life = p.get("life")
        image = p.get("image") if isinstance(p.get("image"), str) else None
        result.append(
            Player(
                slot=slot,
                id=str(p.get("id")),
                name=names[p.get("id")],
                life=life if isinstance(life, (int, float)) else None,
                alive=p.get("alive", True) is not False,
                color=p.get("color") if isinstance(p.get("color"), str) else None,
                image=image,
                commander=(commanders or {}).get(card_id_from_image(image) or ""),
                counters=dict(p.get("counters") or {}),
                commander_damage=cmd,
                commander_damage_max=cmd_max,
                is_turn=turn.get("player") == p.get("id"),
                is_winner=game.get("winner") == p.get("id"),
                is_monarch=game.get("monarch") == p.get("id"),
                has_initiative=game.get("initiative") == p.get("id"),
            )
        )
    return result


class LotusCoordinator(DataUpdateCoordinator[LotusData]):
    """Push-based coordinator fed by the Firebase stream."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        super().__init__(hass, _LOGGER, name=DOMAIN, config_entry=entry)
        self.entry = entry
        self._stream: LotusGameStream | None = None
        self._raw_meta: str | None = None
        self._raw_state: str | None = None
        self._last_meta_at = 0.0
        self._unsub_timer = None
        self._commanders: dict[str, str | None] = {}
        self._commander_lookups: set[str] = set()
        self._log: GameLog | None = None
        self._write_lock = asyncio.Lock()
        self.history: list[dict[str, Any]] = []
        self.stats: dict[str, Any] = summarize([])
        self.slot_listeners: list[Any] = []
        self.data = LotusData()

    @property
    def slots(self) -> int:
        return int(self.entry.data.get(CONF_SLOTS, DEFAULT_SLOTS))

    async def _async_update_data(self) -> LotusData:
        return self.data

    async def async_start(self) -> None:
        self.history = await self.hass.async_add_executor_job(
            read_records, self.hass.config.path(GAMES_FILE)
        )
        self.stats = summarize(self.history)
        self._unsub_timer = async_track_time_interval(
            self.hass, self._check_host, timedelta(seconds=15)
        )
        await self.async_set_code(self.entry.data.get(CONF_CODE), persist=False)

    async def async_shutdown(self) -> None:
        if self._unsub_timer:
            self._unsub_timer()
            self._unsub_timer = None
        if self._stream:
            await self._stream.stop()
            self._stream = None
        await super().async_shutdown()

    async def async_set_code(self, value: str | None, persist: bool = True) -> None:
        """Switch to another game code (empty stops following)."""
        code = normalize_code(value) if value else None
        if value and not code:
            raise ValueError(f"Invalid game code: {value}")
        if self._stream and self._stream.code == code:
            return
        if self._stream:
            await self._stream.stop()
            self._stream = None
        if persist and self.entry.data.get(CONF_CODE) != code:
            self.hass.config_entries.async_update_entry(
                self.entry, data={**self.entry.data, CONF_CODE: code}
            )
        self._raw_meta = self._raw_state = None
        self._last_meta_at = 0.0
        self._log = None
        self.async_set_updated_data(
            LotusData(code=code, status=STATUS_CONNECTING if code else STATUS_IDLE)
        )
        if code:
            self._stream = LotusGameStream(
                async_get_clientsession(self.hass),
                code,
                self._handle_tree,
                self._handle_connection,
            )
            self._stream.start()

    @callback
    def _handle_connection(self, state: str) -> None:
        data = self.data
        if state == "open":
            data.connected = True
        elif state == "denied":
            data.connected = False
            data.status = STATUS_NOT_FOUND
        else:
            data.connected = False
            if data.status in (STATUS_LIVE, STATUS_CONNECTING):
                data.status = STATUS_DISCONNECTED
        self.async_set_updated_data(data)

    @callback
    def _handle_tree(self, tree: dict[str, Any] | None) -> None:
        tree = tree or {}
        meta = tree.get("meta") if isinstance(tree.get("meta"), dict) else None
        raw_meta = json.dumps(meta, sort_keys=True)
        state = tree.get("state")
        first = self._raw_meta is None
        meta_changed = raw_meta != self._raw_meta
        state_changed = isinstance(state, str) and state != self._raw_state
        if not (meta_changed or state_changed or first):
            return
        data = self.data
        data.connected = True
        if meta_changed or first:
            self._raw_meta = raw_meta
            self._last_meta_at = time.monotonic()
            data.meta = meta
        if meta is None:
            data.status = STATUS_NOT_FOUND if data.game is None else STATUS_ENDED
        elif meta.get("status") == "ended":
            data.status = STATUS_ENDED
        else:
            data.status = STATUS_LIVE
        data.host_online = data.status == STATUS_LIVE
        if state_changed:
            self._raw_state = state
            try:
                game = json.loads(state)
            except ValueError:
                game = None
            if isinstance(game, dict) and game.get("players"):
                previous = data.game
                data.game = game
                old_players = data.players
                data.players = parse_players(game, self._commanders)
                self._grow_slots(len(data.players))
                self._fire_events(previous, game, old_players, data.players)
                self._track_game(previous, game, old_players)
                self._resolve_commanders()
        self.async_set_updated_data(data)

    @callback
    def _track_game(
        self,
        prev: dict[str, Any] | None,
        game: dict[str, Any],
        old_players: list[Player],
    ) -> None:
        """Feed the game log and save the game once it has a winner."""
        log = self._log
        if log is None or log.game_no != game.get("gameNo"):
            # A game dropped after someone was knocked out is kept, without a winner.
            if log and prev and not log.winner and log.eliminated:
                self._save_game(log, prev, old_players)
            self._log = GameLog(self.data.code, game)
            return
        log.update(prev, game)
        winner = game.get("winner")
        if winner != log.winner:
            log.winner = winner
            if winner:
                self._save_game(log, game, self.data.players)

    @callback
    def _save_game(
        self, log: GameLog, game: dict[str, Any], players: list[Player]
    ) -> None:
        record = log.record(game, players)
        self.history = [g for g in self.history if g["id"] != record["id"]]
        self.history.append(record)
        self.stats = summarize(self.history)
        self.hass.bus.async_fire(
            EVENT_GAME_RECORDED, {k: v for k, v in record.items() if k != "hits"}
        )
        self.entry.async_create_task(
            self.hass, self._write_game(record), name="lotus_mtg save game"
        )

    async def _write_game(self, record: dict[str, Any]) -> None:
        path = self.hass.config.path(GAMES_FILE)
        async with self._write_lock:
            try:
                await self.hass.async_add_executor_job(write_record, path, record)
            except OSError as err:
                _LOGGER.warning("Could not save Lotus game to %s: %s", path, err)

    @callback
    def _resolve_commanders(self) -> None:
        """Look up the names of commanders seen for the first time."""
        for player in self.data.players:
            card_id = card_id_from_image(player.image)
            if (
                not card_id
                or card_id in self._commanders
                or card_id in self._commander_lookups
            ):
                continue
            self._commander_lookups.add(card_id)
            self.entry.async_create_background_task(
                self.hass,
                self._lookup_commander(card_id),
                name=f"lotus_mtg commander {card_id}",
            )

    async def _lookup_commander(self, card_id: str) -> None:
        try:
            name = await fetch_card_name(async_get_clientsession(self.hass), card_id)
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as err:
            # Not cached, so the next state update tries again.
            _LOGGER.debug("Scryfall lookup for %s failed: %s", card_id, err)
            return
        finally:
            self._commander_lookups.discard(card_id)
        self._commanders[card_id] = name
        data = self.data
        if name and data.game:
            data.players = parse_players(data.game, self._commanders)
            self.async_set_updated_data(data)

    @callback
    def _check_host(self, _now: Any = None) -> None:
        data = self.data
        online = (
            data.status == STATUS_LIVE
            and data.connected
            and time.monotonic() - self._last_meta_at < HOST_OFFLINE_AFTER
        )
        if online != data.host_online:
            data.host_online = online
            self.async_set_updated_data(data)

    @callback
    def _grow_slots(self, count: int) -> None:
        count = min(count, MAX_SLOTS)
        if count <= self.slots:
            return
        old = self.slots
        self.hass.config_entries.async_update_entry(
            self.entry, data={**self.entry.data, CONF_SLOTS: count}
        )
        for listener in self.slot_listeners:
            listener(old, count)

    @callback
    def _fire_events(
        self,
        prev: dict[str, Any] | None,
        game: dict[str, Any],
        old_players: list[Player],
        players: list[Player],
    ) -> None:
        if prev is None:
            return
        bus = self.hass.bus
        base = {"code": self.data.code}
        if prev.get("gameNo") != game.get("gameNo"):
            bus.async_fire(EVENT_NEW_GAME, {**base, "game_no": game.get("gameNo")})
            return
        old_by_id = {p.id: p for p in old_players}
        for p in players:
            info = {**base, "slot": p.slot, "player_id": p.id, "player_name": p.name}
            old = old_by_id.get(p.id)
            if old is None:
                continue
            if (
                old.life is not None
                and p.life is not None
                and old.life != p.life
            ):
                bus.async_fire(
                    EVENT_LIFE_CHANGED,
                    {
                        **info,
                        "old_life": old.life,
                        "new_life": p.life,
                        "delta": p.life - old.life,
                    },
                )
            if old.alive and not p.alive:
                bus.async_fire(EVENT_PLAYER_ELIMINATED, info)
            if p.is_winner and not old.is_winner:
                bus.async_fire(
                    EVENT_GAME_WON,
                    {
                        **info,
                        "commander": p.commander,
                        "image": p.image,
                        "game_no": game.get("gameNo"),
                        "winner_text": game.get("winnerText"),
                    },
                )
        old_turn = prev.get("turn") if isinstance(prev.get("turn"), dict) else {}
        turn = game.get("turn") if isinstance(game.get("turn"), dict) else {}
        if turn and (
            turn.get("player") != old_turn.get("player")
            or turn.get("number") != old_turn.get("number")
        ):
            active = next((p for p in players if p.id == turn.get("player")), None)
            bus.async_fire(
                EVENT_TURN_CHANGED,
                {
                    **base,
                    "turn": turn.get("number"),
                    "slot": active.slot if active else None,
                    "player_id": turn.get("player"),
                    "player_name": active.name if active else None,
                },
            )
