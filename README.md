# Lotus MTG Life Counter for Home Assistant

Follow a live [Lotus](https://lifecounter.app) Magic: The Gathering game in Home Assistant: life totals, commander damage, poison, turns, winner. Push-based (same live stream the `lifecounter.app/watch/<CODE>` page uses), so updates arrive within a second.

## Install (HACS)

1. HACS → ⋮ → **Custom repositories** → add `https://github.com/myklek/Lotus-HACS`, category **Integration**.
2. Install **Lotus MTG Life Counter**, restart Home Assistant.
3. Settings → Devices & services → **Add integration** → *Lotus MTG*. The game code is optional here.

## New game = new code

In Lotus: **Menu → Cast to TV** shows a 5-character code. Put it in Home Assistant by either:

- the **`text.lotus_mtg_game_code`** entity (put it on your dashboard), or
- the service `lotus_mtg.set_game_code` with `code: AZA98`.

A full watch link (`https://lifecounter.app/watch/AZA98`) works too. Empty value stops following.

## Entities

Game:

| Entity | |
|---|---|
| `sensor.lotus_mtg_game_status` | `idle`, `connecting`, `live`, `ended`, `not_found`, `disconnected`; attributes hold every player |
| `sensor.lotus_mtg_turn` | Turn number |
| `sensor.lotus_mtg_active_player` | Whose turn it is (attributes include their `rgb_color`) |
| `sensor.lotus_mtg_winner` | Winner's name; attributes: `slot`, `commander`, `image`, `game_no`, `winner_text` |
| `sensor.lotus_mtg_players_alive` | Count |
| `binary_sensor.lotus_mtg_host_online` | Off when the host phone is asleep/offline (>90 s without updates) |
| `binary_sensor.lotus_mtg_connected` | Stream connection (diagnostic) |

Per seat `N` (Player 1 = top-left seat, entities stay the same across games; unused seats are unavailable):

| Entity | |
|---|---|
| `sensor.lotus_mtg_player_N_life` | Life; attributes: name, color, `rgb_color`, poison, commander damage, counters, monarch, initiative |
| `sensor.lotus_mtg_player_N_name` | Nickname |
| `sensor.lotus_mtg_player_N_commander` | Commander name, see below |
| `sensor.lotus_mtg_player_N_poison` | Poison counters |
| `sensor.lotus_mtg_player_N_commander_damage` | Highest damage from a single commander (21 = lethal); `from` attribute has the breakdown |
| `binary_sensor.lotus_mtg_player_N_turn` | On during that player's turn |
| `binary_sensor.lotus_mtg_player_N_alive` | Off once eliminated |

Seats start at 4 and grow automatically when a bigger game is seen (up to 8).

Lotus does not send commander names, only the card art a player picked as their background. The integration looks that card up on [Scryfall](https://scryfall.com) to get the name, so a commander is only known for players who set their commander as background in Lotus.

## Events

| Event | Data |
|---|---|
| `lotus_mtg_life_changed` | `slot`, `player_name`, `old_life`, `new_life`, `delta` |
| `lotus_mtg_turn_changed` | `turn`, `slot`, `player_name` |
| `lotus_mtg_player_eliminated` | `slot`, `player_name` |
| `lotus_mtg_game_won` | `slot`, `player_name`, `commander`, `image`, `game_no`, `winner_text` |
| `lotus_mtg_new_game` | `game_no` |
| `lotus_mtg_game_recorded` | The saved game without its `hits` list, see below |

## Game history

Every game that ends with a winner is appended as one JSON line to `lotus_mtg_games.jsonl` in the Home Assistant config directory. A game that is dropped after someone was knocked out is saved too, with `winner` empty.

| Field | |
|---|---|
| `id`, `code`, `game_no`, `started`, `ended`, `duration_s`, `turns` | Which game and how long it took |
| `winner`, `winner_slot`, `winner_commander`, `winner_text` | Who won and with what |
| `players` | Per player: `name`, `commander`, final `life`, `place` (1 = winner), `eliminated_at`, `eliminated_by`, `commander_damage_taken`, `poison` |
| `damage` | Totals per `from` / `to` / `kind` |
| `hits` | Every hit in order: `at`, `turn`, `from`, `to`, `kind`, `amount` |
| `partial` | True when Home Assistant joined a game that was already under way |

`kind` is `commander`, `life` or `gain`. Taps on the same player within 3 seconds count as one hit, and a tap that is taken back leaves no trace.

Lotus only says who dealt damage for commander damage, so `from` is exact there. Plain life loss is credited to the player whose turn it is, which needs the turn tracker in Lotus to be on; without it `from` stays empty.

Standings over all saved games (they survive restarts, the file is read at startup):

| Entity | |
|---|---|
| `sensor.lotus_mtg_games_recorded` | Games with a winner; attributes: `unfinished`, `last_game` |
| `sensor.lotus_mtg_most_wins` | Player with the most wins; `leaderboard` attribute lists `player`, `games`, `wins`, `win_rate` |
| `sensor.lotus_mtg_top_commander` | Commander with the most wins; `commanders` attribute lists `commander`, `player`, `games`, `wins`, `win_rate` |
| `sensor.lotus_mtg_top_attacker` | Player who dealt the most attributed damage; `attackers` has totals, `pairs` has `from`, `to`, `damage`, `commander_damage`, `knockouts` |

A leaderboard for a Markdown card:

```yaml
type: markdown
content: >-
  | Player | Games | Wins | Win rate |

  |---|---|---|---|

  {% for row in state_attr('sensor.lotus_mtg_most_wins', 'leaderboard') or [] %}
  | {{ row.player }} | {{ row.games }} | {{ row.wins }} | {{ row.win_rate }}% |

  {% endfor %}
```

For anything deeper, read the file directly:

```python
import pandas as pd

games = pd.read_json("lotus_mtg_games.jsonl", lines=True)
print(games.groupby(["winner", "winner_commander"]).size())
hits = pd.json_normalize(games.to_dict("records"), "hits", ["id"])
print(hits[hits.kind != "gain"].pivot_table(
    index="from", columns="to", values="amount", aggfunc="sum", fill_value=0))
```

## Automation examples

Light follows the active player's color:

```yaml
automation:
  - alias: Lotus - turn color
    trigger:
      - platform: state
        entity_id: sensor.lotus_mtg_active_player
    condition: "{{ state_attr('sensor.lotus_mtg_active_player', 'rgb_color') is not none }}"
    action:
      - service: light.turn_on
        target: { entity_id: light.living_room }
        data:
          rgb_color: "{{ state_attr('sensor.lotus_mtg_active_player', 'rgb_color') }}"
```

Flash red on big hits (5+ damage at once):

```yaml
  - alias: Lotus - big hit
    trigger:
      - platform: event
        event_type: lotus_mtg_life_changed
    condition: "{{ trigger.event.data.delta <= -5 }}"
    action:
      - service: light.turn_on
        target: { entity_id: light.living_room }
        data: { color_name: red, flash: short }
```

Celebrate the winner:

```yaml
  - alias: Lotus - winner
    trigger:
      - platform: event
        event_type: lotus_mtg_game_won
    action:
      - service: light.turn_on
        target: { entity_id: light.living_room }
        data: { effect: colorloop }
      - service: notify.notify
        data:
          message: >-
            {{ trigger.event.data.player_name }} wins
            {%- if trigger.event.data.commander %} with {{ trigger.event.data.commander }}{% endif %}!
```

## Development

```bash
pip install -r requirements_test.txt
pytest
```

Tests run against a fake Firebase stream. Not affiliated with Lotus / lifecounter.app.
