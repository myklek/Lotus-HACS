"""Constants for the Lotus MTG Life Counter integration."""

DOMAIN = "lotus_mtg"

CONF_CODE = "code"
CONF_SLOTS = "slots"

# Public web config of lifecounter.app (same values the /watch page ships).
API_KEY = "AIzaSyAVejTjrcY0SJgb3k81eAfK_rkk6CiRVBk"
DB_URL = "https://lifecounter-app-default-rtdb.europe-west1.firebasedatabase.app"
AUTH_URL = "https://identitytoolkit.googleapis.com"
TOKEN_URL = "https://securetoken.googleapis.com"
WATCH_URL = "https://lifecounter.app/watch/"

# The watch page treats the host as offline after 90 s without a meta update.
HOST_OFFLINE_AFTER = 90

DEFAULT_SLOTS = 4
MAX_SLOTS = 8

STATUS_IDLE = "idle"
STATUS_CONNECTING = "connecting"
STATUS_LIVE = "live"
STATUS_ENDED = "ended"
STATUS_NOT_FOUND = "not_found"
STATUS_DISCONNECTED = "disconnected"

EVENT_LIFE_CHANGED = f"{DOMAIN}_life_changed"
EVENT_PLAYER_ELIMINATED = f"{DOMAIN}_player_eliminated"
EVENT_GAME_WON = f"{DOMAIN}_game_won"
EVENT_TURN_CHANGED = f"{DOMAIN}_turn_changed"
EVENT_NEW_GAME = f"{DOMAIN}_new_game"

SERVICE_SET_GAME_CODE = "set_game_code"

COUNTER_NAMES = {
    "poison": "Poison",
    "tax-1": "Commander tax",
    "tax-2": "Partner tax",
    "storm": "Storm",
    "xp": "Experience",
    "energy": "Energy",
    "rad": "Radiation",
    "treasure": "Treasure",
    "clue": "Clue",
    "food": "Food",
    "speed": "Speed",
    "gold": "Gold",
    "map": "Map",
    "blood": "Blood",
    "W": "White mana",
    "U": "Blue mana",
    "B": "Black mana",
    "R": "Red mana",
    "G": "Green mana",
    "C": "Colorless mana",
    "aC": "Counter A",
    "bC": "Counter B",
    "cC": "Counter C",
}

# Approximate RGB for Lotus' named player colors (handy for light automations).
PLAYER_COLORS = {
    "red": (229, 57, 53),
    "darkRed": (139, 0, 0),
    "pink": (236, 64, 122),
    "orange": (251, 140, 0),
    "yellow": (253, 216, 53),
    "brightGreen": (118, 255, 3),
    "babyGreen": (174, 213, 129),
    "sand": (215, 190, 140),
    "green": (67, 160, 71),
    "seaGreen": (38, 166, 154),
    "blue": (30, 136, 229),
    "babyBlue": (129, 212, 250),
    "lightBlue": (79, 195, 247),
    "sandBlue": (120, 144, 156),
    "lightPurple": (179, 157, 219),
    "purple": (142, 36, 170),
    "grey": (158, 158, 158),
    "lightGrey": (224, 224, 224),
    "white": (255, 255, 255),
}
