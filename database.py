"""MongoDB storage – everything survives bot restarts / Heroku dyno restarts."""
from datetime import datetime, timezone

from motor.motor_asyncio import AsyncIOMotorClient

import config

_client = AsyncIOMotorClient(config.MONGO_URI)
db = _client[config.DB_NAME]

settings_col = db.settings
accounts_col = db.accounts
counters_col = db.counters

KINDS = ("user", "channel", "group")
FIELDS = ("name", "bio", "pfp", "username")


# ---------------- settings ----------------
async def get_settings() -> dict:
    doc = await settings_col.find_one({"_id": "settings"})
    if not doc:
        doc = {"_id": "settings", "auto": False, **{k: {} for k in KINDS}}
        await settings_col.insert_one(doc)
    for k in KINDS:
        doc.setdefault(k, {})
    doc.setdefault("auto", False)
    return doc


async def set_field(kind: str, field: str, value):
    await settings_col.update_one({"_id": "settings"}, {"$set": {f"{kind}.{field}": value}}, upsert=True)


async def clear_kind(kind: str):
    await settings_col.update_one({"_id": "settings"}, {"$set": {kind: {}}}, upsert=True)


async def toggle_auto() -> bool:
    s = await get_settings()
    new = not s.get("auto", False)
    await settings_col.update_one({"_id": "settings"}, {"$set": {"auto": new}}, upsert=True)
    return new


# ---------------- accounts ----------------
async def save_account(user_id: int, session: str, info: dict, original: dict | None):
    existing = await accounts_col.find_one({"_id": user_id})
    update = {"session": session, "info": info, "updated": datetime.now(timezone.utc)}
    if not existing:
        update.update({
            "original": original or {},
            "chats": {"channel": [], "group": []},
            "added": datetime.now(timezone.utc),
        })
    elif not existing.get("original") and original:
        update["original"] = original
    await accounts_col.update_one({"_id": user_id}, {"$set": update}, upsert=True)


async def get_account(user_id: int):
    return await accounts_col.find_one({"_id": user_id})


async def all_accounts() -> list[dict]:
    return [a async for a in accounts_col.find({}).sort("added", 1)]


async def update_account(user_id: int, data: dict):
    await accounts_col.update_one({"_id": user_id}, {"$set": data})


async def remove_account(user_id: int):
    await accounts_col.delete_one({"_id": user_id})


# ---------------- username counters ----------------
async def get_counter(key: str) -> int:
    doc = await counters_col.find_one({"_id": key})
    return doc["n"] if doc else 0


async def set_counter(key: str, n: int):
    await counters_col.update_one({"_id": key}, {"$set": {"n": n}}, upsert=True)


async def reset_counter(key: str):
    await counters_col.delete_one({"_id": key})


# ---------------- UI (start pic + button emojis) ----------------
async def get_ui() -> dict:
    doc = await settings_col.find_one({"_id": "ui"}) or {}
    doc.setdefault("start_pic", None)
    doc.setdefault("emojis", {})
    doc.setdefault("icons", True)
    return doc


async def set_ui(field: str, value):
    await settings_col.update_one({"_id": "ui"}, {"$set": {field: value}}, upsert=True)


async def unset_ui(field: str):
    await settings_col.update_one({"_id": "ui"}, {"$unset": {field: ""}}, upsert=True)


async def ping():
    await _client.admin.command("ping")
