"""Per-game record of who hit whom, built from successive game states.

Kept free of Home Assistant imports so it can be tested on its own.
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
import os
import time
from typing import Any

# Taps on the same player from the same source within this window count as one hit.
MERGE_WINDOW_MS = 3000

KIND_COMMANDER = "commander"
KIND_LIFE = "life"
KIND_GAIN = "gain"


def _to_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _players(game: dict[str, Any] | None) -> list[dict[str, Any]]:
    return [p for p in (game or {}).get("players") or [] if isinstance(p, dict)]


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _cmd_total(player: dict[str, Any], source: str) -> int:
    value = _dict(player.get("cmd")).get(source)
    return sum(_to_int(v) for v in (value if isinstance(value, list) else [value]))


def _alive(player: dict[str, Any]) -> bool:
    return player.get("alive", True) is not False


def _iso(ms: int | None) -> str | None:
    if not ms:
        return None
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).isoformat(
        timespec="seconds"
    )


class GameLog:
    """Collects the hits and eliminations of one game."""

    def __init__(self, code: str | None, game: dict[str, Any]) -> None:
        self.code = code
        self.game_no = game.get("gameNo")
        self.started_ms = _to_int(_dict(game.get("clock")).get("start")) or int(
            time.time() * 1000
        )
        self.id = f"{code}-{self.game_no}-{self.started_ms}"
        # A winner that was already there is not ours to record again.
        self.winner = game.get("winner")
        self.hits: list[dict[str, Any]] = []
        self.eliminated: dict[str, dict[str, Any]] = {}
        starting = game.get("startingLife")
        self.partial = any(
            not _alive(p) or p.get("cmd") or (starting and p.get("life") != starting)
            for p in _players(game)
        )

    def update(self, prev: dict[str, Any] | None, game: dict[str, Any]) -> None:
        """Record what changed between two states of this game."""
        at = _to_int(game.get("sentAt")) or int(time.time() * 1000)
        turn = _dict(game.get("turn"))
        before = {p.get("id"): p for p in _players(prev)}
        for player in _players(game):
            pid = player.get("id")
            old = before.get(pid)
            if old is None:
                continue
            lost = 0
            if isinstance(old.get("life"), (int, float)) and isinstance(
                player.get("life"), (int, float)
            ):
                lost = int(old["life"] - player["life"])
            from_commanders = 0
            for source in {*_dict(old.get("cmd")), *_dict(player.get("cmd"))}:
                amount = _cmd_total(player, source) - _cmd_total(old, source)
                if amount:
                    self._hit(at, source, pid, KIND_COMMANDER, amount, turn)
                    from_commanders += amount
            # Lotus can be set to leave life alone when commander damage is dealt.
            if lost:
                lost -= from_commanders
            if lost:
                active = turn.get("player")
                source = active if active and active != pid else None
                self._hit(at, source, pid, KIND_LIFE, lost, turn)
            if _alive(old) and not _alive(player):
                self.eliminated[pid] = {"at": at, "by": self._last_source(pid)}
            elif _alive(player):
                self.eliminated.pop(pid, None)

    def _hit(
        self,
        at: int,
        source: str | None,
        target: str,
        kind: str,
        amount: int,
        turn: dict[str, Any],
    ) -> None:
        last = next(
            (
                h
                for h in reversed(self.hits)
                if h["to"] == target and h["from"] == source and h["kind"] == kind
            ),
            None,
        )
        if last and at - last["end"] <= MERGE_WINDOW_MS:
            last["amount"] += amount
            last["end"] = at
            if not last["amount"]:
                self.hits.remove(last)
            return
        self.hits.append(
            {
                "at": at,
                "end": at,
                "from": source,
                "to": target,
                "kind": kind,
                "amount": amount,
                "turn": turn.get("number"),
            }
        )

    def _last_source(self, target: str) -> str | None:
        for hit in reversed(self.hits):
            if hit["to"] == target and hit["amount"] > 0:
                return hit["from"]
        return None

    def record(self, game: dict[str, Any], players: list[Any]) -> dict[str, Any]:
        """Summary of the game; players are the coordinator's parsed players."""
        names = {p.id: p.name for p in players}

        def name(pid: str | None) -> str | None:
            return names.get(pid, pid) if pid else None

        clock = _dict(game.get("clock"))
        ended_ms = (
            _to_int(clock.get("pausedAt"))
            or _to_int(game.get("sentAt"))
            or int(time.time() * 1000)
        )
        winner_id = game.get("winner")
        winner = next((p for p in players if p.id == winner_id), None)
        order = sorted(self.eliminated, key=lambda pid: self.eliminated[pid]["at"])

        hits = []
        totals: dict[tuple[str | None, str, str], int] = {}
        for hit in self.hits:
            kind, amount, source = hit["kind"], hit["amount"], hit["from"]
            if amount < 0:
                if kind != KIND_LIFE:
                    continue
                kind, amount, source = KIND_GAIN, -amount, None
            hits.append(
                {
                    "at": _iso(hit["at"]),
                    "turn": hit["turn"],
                    "from": name(source),
                    "to": name(hit["to"]),
                    "kind": kind,
                    "amount": amount,
                }
            )
            if kind != KIND_GAIN:
                key = (name(source), name(hit["to"]), kind)
                totals[key] = totals.get(key, 0) + amount

        return {
            "id": self.id,
            "code": self.code,
            "game_no": self.game_no,
            "started": _iso(self.started_ms),
            "ended": _iso(ended_ms),
            "duration_s": max(0, (ended_ms - self.started_ms) // 1000),
            "turns": _dict(game.get("turn")).get("number"),
            "partial": self.partial,
            "winner": winner.name if winner else None,
            "winner_slot": winner.slot if winner else None,
            "winner_commander": winner.commander if winner else None,
            "winner_text": game.get("winnerText") if winner else None,
            "players": [
                {
                    "slot": p.slot,
                    "name": p.name,
                    "commander": p.commander,
                    "color": p.color,
                    "life": p.life,
                    "alive": p.alive,
                    "place": (
                        len(players) - order.index(p.id)
                        if p.id in self.eliminated
                        else (1 if p.id == winner_id else None)
                    ),
                    "eliminated_at": _iso(self.eliminated.get(p.id, {}).get("at")),
                    "eliminated_by": name(self.eliminated.get(p.id, {}).get("by")),
                    "commander_damage_taken": p.commander_damage,
                    "poison": p.poison,
                }
                for p in players
            ],
            "damage": [
                {"from": source, "to": target, "kind": kind, "amount": amount}
                for (source, target, kind), amount in totals.items()
            ],
            "hits": hits,
        }


def read_records(path: str) -> list[dict[str, Any]]:
    """Load the saved games, skipping lines that are not valid records."""
    records = []
    try:
        with open(path, encoding="utf-8") as handle:
            for line in handle:
                try:
                    record = json.loads(line)
                except ValueError:
                    continue
                if isinstance(record, dict) and record.get("id"):
                    records.append(record)
    except FileNotFoundError:
        pass
    return records


def write_record(path: str, record: dict[str, Any]) -> None:
    """Append a game to a JSON Lines file, replacing an earlier record of it."""
    lines = [
        json.dumps(old, ensure_ascii=False)
        for old in read_records(path)
        if old["id"] != record["id"]
    ]
    lines.append(json.dumps(record, ensure_ascii=False))
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        handle.write("\n".join(lines) + "\n")
    os.replace(tmp, path)


def _rate(wins: int, games: int) -> float:
    return round(100 * wins / games, 1) if games else 0.0


def summarize(games: list[dict[str, Any]]) -> dict[str, Any]:
    """Standings over all saved games: wins, commanders and who hit whom."""
    finished = [g for g in games if g.get("winner")]
    players: dict[str, dict[str, Any]] = {}
    commanders: dict[tuple[str, str], dict[str, Any]] = {}
    for game in finished:
        for p in game.get("players") or []:
            won = int(p.get("name") == game["winner"])
            row = players.setdefault(
                p.get("name"), {"player": p.get("name"), "games": 0, "wins": 0}
            )
            row["games"] += 1
            row["wins"] += won
            if p.get("commander"):
                row = commanders.setdefault(
                    (p["commander"], p.get("name")),
                    {
                        "commander": p["commander"],
                        "player": p.get("name"),
                        "games": 0,
                        "wins": 0,
                    },
                )
                row["games"] += 1
                row["wins"] += won
    for row in (*players.values(), *commanders.values()):
        row["win_rate"] = _rate(row["wins"], row["games"])

    # Damage counts for every saved game, also the ones nobody won.
    pairs: dict[tuple[str, str], dict[str, Any]] = {}

    def pair(source: str, target: str) -> dict[str, Any]:
        return pairs.setdefault(
            (source, target),
            {
                "from": source,
                "to": target,
                "damage": 0,
                "commander_damage": 0,
                "knockouts": 0,
            },
        )

    for game in games:
        for hit in game.get("damage") or []:
            if not hit.get("from"):
                continue
            row = pair(hit["from"], hit["to"])
            row["damage"] += _to_int(hit.get("amount"))
            if hit.get("kind") == KIND_COMMANDER:
                row["commander_damage"] += _to_int(hit.get("amount"))
        for p in game.get("players") or []:
            if p.get("eliminated_by"):
                pair(p["eliminated_by"], p.get("name"))["knockouts"] += 1
    attackers: dict[str, dict[str, Any]] = {}
    for row in pairs.values():
        total = attackers.setdefault(
            row["from"], {"player": row["from"], "damage": 0, "knockouts": 0}
        )
        total["damage"] += row["damage"]
        total["knockouts"] += row["knockouts"]

    def by_wins(row: dict[str, Any]) -> tuple[Any, ...]:
        return (-row["wins"], -row["win_rate"], str(row.get("commander") or row["player"]))

    def by_damage(row: dict[str, Any]) -> tuple[Any, ...]:
        return (-row["damage"], -row["knockouts"], str(row.get("from") or row["player"]))

    last = finished[-1] if finished else None
    return {
        "games": len(finished),
        "unfinished": len(games) - len(finished),
        "players": sorted(players.values(), key=by_wins),
        "commanders": sorted(commanders.values(), key=by_wins),
        "attackers": sorted(attackers.values(), key=by_damage),
        "pairs": sorted(pairs.values(), key=by_damage),
        "last": (
            {
                key: last.get(key)
                for key in ("ended", "winner", "winner_commander", "duration_s")
            }
            if last
            else None
        ),
    }
