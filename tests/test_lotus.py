"""Tests against a fake Firebase RTDB SSE endpoint."""

import asyncio
import json
from unittest.mock import patch

from aiohttp import web
from aiohttp.test_utils import TestServer
import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_capture_events,
)

from custom_components.lotus_mtg.client import (
    apply_event,
    card_id_from_image,
    normalize_code,
)
from custom_components.lotus_mtg.const import DOMAIN
from custom_components.lotus_mtg.coordinator import parse_players
from custom_components.lotus_mtg.gamelog import (
    GameLog,
    read_records,
    summarize,
    write_record,
)


def test_normalize_code():
    assert normalize_code("aza98") == "AZA98"
    assert normalize_code("https://lifecounter.app/watch/AZA98") == "AZA98"
    assert normalize_code(" k7q-x2 ") == "K7QX2"
    assert normalize_code("ABCDI") is None  # I is not in the alphabet
    assert normalize_code("") is None


def test_apply_event():
    tree = apply_event(None, "/", {"meta": {"status": "live"}, "state": "{}"}, False)
    tree = apply_event(tree, "/meta", {"status": "ended", "x/y": 1}, True)
    assert tree["meta"] == {"status": "ended", "x": {"y": 1}}
    tree = apply_event(tree, "/state", None, False)
    assert "state" not in tree


JARAD_ID = "c6717954-35d8-4d7e-95aa-f7d26d15d4b2"
JARAD_IMAGE = (
    f"https://cards.scryfall.io/art_crop/front/c/6/{JARAD_ID}.jpg?1592673383"
)


def test_card_id_from_image():
    assert card_id_from_image(JARAD_IMAGE) == JARAD_ID
    assert card_id_from_image("https://example.com/" + JARAD_ID + ".jpg") is None
    assert card_id_from_image(None) is None


def state(players, **extra):
    return json.dumps({"players": players, "gameNo": 1, **extra})


P1 = {"id": "a", "nick": "Mykolas", "life": 40, "alive": True, "color": "red"}
P2 = {"id": "b", "nick": "Tomas", "life": 40, "alive": True, "color": "#1e88e5"}


@pytest.fixture(autouse=True)
def saved_games():
    """Keep finished games out of the config directory and collect them instead."""
    games = []
    with patch(
        "custom_components.lotus_mtg.coordinator.write_record",
        side_effect=lambda path, record: games.append(record),
    ):
        yield games


def test_game_log():
    def game(a, b, c, sent, **extra):
        return {
            "gameNo": 3,
            "startingLife": 40,
            "clock": {"start": 1_000_000},
            "sentAt": sent,
            "players": [
                {"id": "a", "nick": "Erikas", **a},
                {"id": "b", "nick": "Mykolas", **b},
                {"id": "c", "nick": "Domas", **c},
            ],
            **extra,
        }

    full = {"life": 40, "alive": True}
    states = [
        game(full, full, full, 1_000_100),
        # two quick taps of commander damage from Erikas merge into one hit
        game(full, {"life": 30, "cmd": {"a": [10]}}, full, 1_010_000),
        game(full, {"life": 19, "cmd": {"a": [21]}, "alive": False}, full, 1_010_400),
        # a mistap on Domas that is corrected leaves no trace
        game(full, {"life": 19, "cmd": {"a": [21]}, "alive": False},
             {"life": 39}, 1_020_000),
        game(full, {"life": 19, "cmd": {"a": [21]}, "alive": False}, full, 1_020_400),
        # plain life loss during Domas' turn is credited to Domas
        game({"life": 0, "alive": False},
             {"life": 19, "cmd": {"a": [21]}, "alive": False}, full, 1_030_000,
             turn={"player": "c", "number": 5}, winner="c", winnerText="Victory",
             clock={"start": 1_000_000, "pausedAt": 1_030_000}),
    ]
    log = GameLog("AZA98", states[0])
    assert not log.partial
    for prev, new in zip(states, states[1:]):
        log.update(prev, new)
    record = log.record(states[-1], parse_players(states[-1], {}))

    assert record["id"] == "AZA98-3-1000000"
    assert record["winner"] == "Domas"
    assert record["duration_s"] == 30
    assert record["turns"] == 5
    assert [(h["from"], h["to"], h["kind"], h["amount"]) for h in record["hits"]] == [
        ("Erikas", "Mykolas", "commander", 21),
        ("Domas", "Erikas", "life", 40),
    ]
    by_name = {p["name"]: p for p in record["players"]}
    assert by_name["Mykolas"]["place"] == 3
    assert by_name["Mykolas"]["eliminated_by"] == "Erikas"
    assert by_name["Mykolas"]["commander_damage_taken"] == {"Erikas": 21}
    assert by_name["Erikas"]["place"] == 2
    assert by_name["Erikas"]["eliminated_by"] == "Domas"
    assert by_name["Domas"]["place"] == 1
    assert {"from": "Erikas", "to": "Mykolas", "kind": "commander", "amount": 21} in (
        record["damage"]
    )

    # joining a game that is already under way is flagged
    assert GameLog("AZA98", states[2]).partial

    stats = summarize([record, {**record, "id": "other", "winner": None}])
    assert stats["games"] == 1
    assert stats["unfinished"] == 1
    assert stats["players"][0] == {
        "player": "Domas", "games": 1, "wins": 1, "win_rate": 100.0,
    }
    assert stats["attackers"][0] == {"player": "Domas", "damage": 80, "knockouts": 2}
    assert {
        "from": "Erikas", "to": "Mykolas", "damage": 42,
        "commander_damage": 42, "knockouts": 2,
    } in stats["pairs"]


def test_write_record(tmp_path):
    path = str(tmp_path / "games.jsonl")
    write_record(path, {"id": "x-1", "winner": "A"})
    write_record(path, {"id": "x-2", "winner": "B"})
    write_record(path, {"id": "x-1", "winner": "C"})  # winner corrected
    with open(path, "a", encoding="utf-8") as handle:
        handle.write("not json\n")
    assert read_records(path) == [
        {"id": "x-2", "winner": "B"},
        {"id": "x-1", "winner": "C"},
    ]
    assert read_records(str(tmp_path / "missing.jsonl")) == []


class FakeFirebase:
    def __init__(self):
        self.queue: asyncio.Queue = asyncio.Queue()
        self.requests = 0
        app = web.Application()
        app.router.add_get("/liveGames/{code}.json", self.handle)
        app.router.add_get("/cards/{card_id}", self.card)
        self.cards = {JARAD_ID: "Jarad, Golgari Lich Lord"}
        self.card_requests = 0
        self.server = TestServer(app, host="127.0.0.1")

    async def handle(self, request):
        self.requests += 1
        resp = web.StreamResponse(headers={"Content-Type": "text/event-stream"})
        await resp.prepare(request)
        while True:
            event, data = await self.queue.get()
            if event is None:
                return resp
            await resp.write(
                f"event: {event}\ndata: {json.dumps(data)}\n\n".encode()
            )

    async def card(self, request):
        self.card_requests += 1
        name = self.cards.get(request.match_info["card_id"])
        if name is None:
            return web.json_response({"object": "error"}, status=404)
        return web.json_response({"object": "card", "name": name})

    async def send(self, event, data):
        await self.queue.put((event, data))


async def settle(hass):
    for _ in range(5):
        await asyncio.sleep(0.05)
        await hass.async_block_till_done()


@pytest.fixture
async def fake(socket_enabled):
    fb = FakeFirebase()
    await fb.server.start_server()
    url = str(fb.server.make_url("")).rstrip("/")
    with (
        patch("custom_components.lotus_mtg.client.DB_URL", url),
        patch("custom_components.lotus_mtg.client.SCRYFALL_URL", url),
    ):
        yield fb
    await fb.queue.put((None, None))
    await fb.server.close()


async def test_full_flow(hass, fake):
    entry = MockConfigEntry(domain=DOMAIN, data={"code": "AZA98"}, unique_id=DOMAIN)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await settle(hass)

    assert hass.states.get("text.lotus_mtg_game_code").state == "AZA98"
    assert hass.states.get("sensor.lotus_mtg_game_status").state == "connecting"

    await fake.send(
        "put",
        {
            "path": "/",
            "data": {
                "meta": {"status": "live"},
                "state": state(
                    [P1, P2],
                    seat=["a", "b"],
                    turn={"player": "a", "number": 1},
                ),
            },
        },
    )
    await settle(hass)
    assert hass.states.get("sensor.lotus_mtg_game_status").state == "live"
    life = hass.states.get("sensor.lotus_mtg_player_1_life")
    assert life.state == "40"
    assert life.attributes["player_name"] == "Mykolas"
    assert life.attributes["rgb_color"] == [229, 57, 53]
    assert hass.states.get("sensor.lotus_mtg_player_2_name").state == "Tomas"
    assert hass.states.get("binary_sensor.lotus_mtg_player_1_turn").state == "on"
    assert hass.states.get("sensor.lotus_mtg_active_player").state == "Mykolas"
    assert hass.states.get("binary_sensor.lotus_mtg_host_online").state == "on"
    assert hass.states.get("sensor.lotus_mtg_player_3_life").state == "unavailable"

    life_events = async_capture_events(hass, "lotus_mtg_life_changed")
    turn_events = async_capture_events(hass, "lotus_mtg_turn_changed")
    won = async_capture_events(hass, "lotus_mtg_game_won")
    out = async_capture_events(hass, "lotus_mtg_player_eliminated")

    p2 = {**P2, "life": 33, "cmd": {"a": [7, 0]}, "counters": {"poison": 2}}
    await fake.send(
        "put",
        {
            "path": "/state",
            "data": state([P1, p2], seat=["a", "b"], turn={"player": "b", "number": 1}),
        },
    )
    await settle(hass)
    assert hass.states.get("sensor.lotus_mtg_player_2_life").state == "33"
    cmd = hass.states.get("sensor.lotus_mtg_player_2_commander_damage")
    assert cmd.state == "7"
    assert cmd.attributes["from"] == {"Mykolas": 7}
    assert hass.states.get("sensor.lotus_mtg_player_2_poison").state == "2"
    assert life_events[0].data["delta"] == -7
    assert life_events[0].data["slot"] == 2
    assert turn_events[0].data["player_name"] == "Tomas"

    p2 = {**p2, "life": 0, "alive": False}
    await fake.send(
        "put",
        {
            "path": "/state",
            "data": state([P1, p2], seat=["a", "b"], winner="a",
                          turn={"player": "b", "number": 1}),
        },
    )
    await settle(hass)
    assert out[0].data["player_name"] == "Tomas"
    assert won[0].data["player_name"] == "Mykolas"
    assert hass.states.get("sensor.lotus_mtg_winner").state == "Mykolas"
    assert hass.states.get("binary_sensor.lotus_mtg_player_2_alive").state == "off"

    # Third player joins -> new slot entities appear
    p3 = {"id": "c", "nick": "Jonas", "life": 40}
    await fake.send(
        "put",
        {"path": "/state", "data": state([P1, P2, p3, {**p3, "id": "d", "nick": "E"},
                                          {**p3, "id": "e", "nick": "F"}])},
    )
    await settle(hass)
    assert hass.states.get("sensor.lotus_mtg_player_5_life").state == "40"
    assert entry.data["slots"] == 5

    # Host stops sharing
    await fake.send("patch", {"path": "/meta", "data": {"status": "ended"}})
    await settle(hass)
    assert hass.states.get("sensor.lotus_mtg_game_status").state == "ended"

    # Change code via the text entity
    await hass.services.async_call(
        "text", "set_value",
        {"entity_id": "text.lotus_mtg_game_code",
         "value": "https://lifecounter.app/watch/k7qx2"},
        blocking=True,
    )
    await settle(hass)
    assert entry.data["code"] == "K7QX2"
    await fake.send("put", {"path": "/", "data": None})
    await settle(hass)
    assert hass.states.get("sensor.lotus_mtg_game_status").state == "not_found"
    assert hass.states.get("sensor.lotus_mtg_player_1_life").state == "unavailable"

    # Service to clear
    await hass.services.async_call(DOMAIN, "set_game_code", {"code": ""}, blocking=True)
    await settle(hass)
    assert hass.states.get("sensor.lotus_mtg_game_status").state == "idle"

    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_winner_commander(hass, fake, saved_games):
    entry = MockConfigEntry(domain=DOMAIN, data={"code": "AZA98"}, unique_id=DOMAIN)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await settle(hass)

    p1 = {**P1, "image": JARAD_IMAGE}
    await fake.send(
        "put",
        {"path": "/", "data": {"meta": {"status": "live"}, "state": state([p1, P2])}},
    )
    await settle(hass)
    commander = hass.states.get("sensor.lotus_mtg_player_1_commander")
    assert commander.state == "Jarad, Golgari Lich Lord"
    assert commander.attributes["image"] == JARAD_IMAGE
    assert hass.states.get("sensor.lotus_mtg_player_2_commander").state == "unknown"

    won = async_capture_events(hass, "lotus_mtg_game_won")
    recorded = async_capture_events(hass, "lotus_mtg_game_recorded")
    # Lotus sends the win as a patch on the root, like every other state change
    await fake.send(
        "patch",
        {
            "path": "/",
            "data": {
                "state": state(
                    [p1, {**P2, "life": 0, "alive": False}],
                    winner="a",
                    winnerText="Uncounterable triumph.",
                ),
                "meta/updatedAt": 1,
            },
        },
    )
    await settle(hass)
    assert won[0].data["player_name"] == "Mykolas"
    assert won[0].data["commander"] == "Jarad, Golgari Lich Lord"
    assert won[0].data["game_no"] == 1
    assert won[0].data["winner_text"] == "Uncounterable triumph."
    winner = hass.states.get("sensor.lotus_mtg_winner")
    assert winner.state == "Mykolas"
    assert winner.attributes["commander"] == "Jarad, Golgari Lich Lord"
    assert fake.card_requests == 1

    assert recorded[0].data["winner_commander"] == "Jarad, Golgari Lich Lord"
    assert "hits" not in recorded[0].data
    assert len(saved_games) == 1
    game = saved_games[0]
    assert game["winner"] == "Mykolas"
    assert [(h["to"], h["kind"], h["amount"]) for h in game["hits"]] == [
        ("Tomas", "life", 40)
    ]
    assert [p["place"] for p in game["players"]] == [1, 2]

    assert hass.states.get("sensor.lotus_mtg_games_recorded").state == "1"
    most_wins = hass.states.get("sensor.lotus_mtg_most_wins")
    assert most_wins.state == "Mykolas"
    assert most_wins.attributes["leaderboard"][0]["win_rate"] == 100.0
    top = hass.states.get("sensor.lotus_mtg_top_commander")
    assert top.state == "Jarad, Golgari Lich Lord"
    assert top.attributes["commanders"][0]["player"] == "Mykolas"
    assert hass.states.get("sensor.lotus_mtg_top_attacker").state == "unknown"

    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_config_flow(hass):
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"code": "nope"}
    )
    assert result["errors"] == {"code": "invalid_code"}
    with patch("custom_components.lotus_mtg.async_setup_entry", return_value=True):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"code": "aza98"}
        )
    assert result["type"] == "create_entry"
    assert result["data"] == {"code": "AZA98"}
