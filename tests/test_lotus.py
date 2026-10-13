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

from custom_components.lotus_mtg.client import apply_event, normalize_code
from custom_components.lotus_mtg.const import DOMAIN


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


def state(players, **extra):
    return json.dumps({"players": players, "gameNo": 1, **extra})


P1 = {"id": "a", "nick": "Mykolas", "life": 40, "alive": True, "color": "red"}
P2 = {"id": "b", "nick": "Tomas", "life": 40, "alive": True, "color": "#1e88e5"}


class FakeFirebase:
    def __init__(self):
        self.queue: asyncio.Queue = asyncio.Queue()
        self.requests = 0
        app = web.Application()
        app.router.add_get("/liveGames/{code}.json", self.handle)
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
    with patch(
        "custom_components.lotus_mtg.client.DB_URL",
        str(fb.server.make_url("")).rstrip("/"),
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
