import os


def _ints(value: str) -> list[int]:
    return [int(x) for x in value.replace(",", " ").split() if x.strip().lstrip("-").isdigit()]


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

# Max numbers tried per username before giving up (base, base1, base2 ...)
USERNAME_TRIES = int(os.environ.get("USERNAME_TRIES", "300"))


def check():
    missing = [k for k, v in {
        "API_ID": API_ID, "API_HASH": API_HASH, "BOT_TOKEN": BOT_TOKEN,
        "MONGO_URI": MONGO_URI, "OWNER_IDS": OWNER_IDS,
    }.items() if not v]
    if missing:
        raise SystemExit(f"Missing environment variables: {', '.join(missing)}")
