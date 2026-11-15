import os


def _ints(value: str) -> list[int]:
    import re
    return [int(x) for x in re.split(r"[\s,;:|]+", str(value or "")) if x.strip().lstrip("-").isdigit()]


API_ID = int(os.environ.get("API_ID", "0"))
API_HASH = os.environ.get("API_HASH", "")
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
MONGO_URI = os.environ.get("MONGO_URI", "")
DB_NAME = os.environ.get("DB_NAME", "khoufu_bot")

# Multiple owners: "123456 987654" or "123456,987654"
OWNER_IDS = _ints(os.environ.get("OWNER_IDS", ""))

CHANNEL_COUNT = int(os.environ.get("CHANNEL_COUNT", "10"))
GROUP_COUNT = int(os.environ.get("GROUP_COUNT", "10"))

# Seconds to wait between Telegram actions (avoid flood bans)
ACTION_DELAY = float(os.environ.get("ACTION_DELAY", "3"))

# Telegram channel/group ID for detailed logs (bot must be admin there), e.g. -1001234567890
_lc = os.environ.get("LOG_CHANNEL", "").strip()
LOG_CHANNEL = int(_lc) if _lc.lstrip("-").isdigit() else (_lc or None)

# How many accounts are processed at the same time
PARALLEL_ACCOUNTS = max(1, int(os.environ.get("PARALLEL_ACCOUNTS", "3")))

# If Telegram asks to wait longer than this (seconds) the step is skipped instead of blocking
MAX_FLOOD_WAIT = int(os.environ.get("MAX_FLOOD_WAIT", "900"))

# Max numbers tried per username before giving up (base, base1, base2 ...)
USERNAME_TRIES = int(os.environ.get("USERNAME_TRIES", "300"))


# ---------------- shop
CURRENCY = os.environ.get("CURRENCY", "₹")
# Unpaid orders are cancelled (and the item goes back to stock) after this many minutes
ORDER_TIMEOUT = max(3, int(os.environ.get("ORDER_TIMEOUT", "15")))
# Optional: must-join channels from env (usernames or -100 ids, space separated).
# Owners can also manage them inside the bot (Shop Admin -> Must-Join).
FORCE_JOIN = os.environ.get("FORCE_JOIN", "").replace(",", " ").split()


def check():
    missing = [k for k, v in {
        "API_ID": API_ID, "API_HASH": API_HASH, "BOT_TOKEN": BOT_TOKEN,
        "MONGO_URI": MONGO_URI, "OWNER_IDS": OWNER_IDS,
    }.items() if not v]
    if missing:
        raise SystemExit(f"Missing environment variables: {', '.join(missing)}")
