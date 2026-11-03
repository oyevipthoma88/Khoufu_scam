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


# ================================================================ SHOP
from pymongo import ReturnDocument  # noqa: E402

users_col = db.users
stock_col = db.stock
orders_col = db.orders
codes_col = db.codes
sellers_col = db.sellers

SHOP_KINDS = ("account", "channel", "group", "bot", "method")


async def ensure_indexes():
    await stock_col.create_index([("kind", 1), ("status", 1)])
    await stock_col.create_index([("kind", 1), ("country", 1), ("status", 1)])
    await orders_col.create_index([("user_id", 1), ("status", 1)])
    await orders_col.create_index([("status", 1), ("created", 1)])


def now():
    return datetime.now(timezone.utc)


# ---------------- shop settings
async def get_shop() -> dict:
    doc = await settings_col.find_one({"_id": "shop"}) or {}
    doc.setdefault("force", [])          # [{"chat": id, "title": str, "link": str}]
    doc.setdefault("prices", {})         # {"account": {"_": 100, "IN": 80}, "channel": 50, ...}
    doc.setdefault("qr", None)
    doc.setdefault("upi", "")
    doc.setdefault("update_link", "")
    doc.setdefault("contact", "")
    doc.setdefault("tutorial", "")
    return doc


async def set_shop(field: str, value):
    await settings_col.update_one({"_id": "shop"}, {"$set": {field: value}}, upsert=True)


# ---------------- users
async def touch_user(u) -> dict:
    doc = await users_col.find_one_and_update(
        {"_id": u.id},
        {"$set": {"name": " ".join(x for x in [u.first_name, u.last_name] if x) or "User",
                  "username": u.username, "seen": now()},
         "$setOnInsert": {"joined": now(), "balance": 0, "verified": False}},
        upsert=True, return_document=ReturnDocument.AFTER)
    return doc


async def get_user(uid: int) -> dict | None:
    return await users_col.find_one({"_id": uid})


async def set_user(uid: int, data: dict):
    await users_col.update_one({"_id": uid}, {"$set": data}, upsert=True)


async def add_balance(uid: int, amount: float):
    await users_col.update_one({"_id": uid}, {"$inc": {"balance": amount}}, upsert=True)


async def take_balance(uid: int, amount: float) -> bool:
    """Atomic: only deducts if the user really has enough."""
    r = await users_col.update_one({"_id": uid, "balance": {"$gte": amount}}, {"$inc": {"balance": -amount}})
    return r.modified_count == 1


async def count_users() -> int:
    return await users_col.count_documents({})


# ---------------- stock
async def add_stock(item: dict):
    item.setdefault("status", "available")
    item["added"] = now()
    return (await stock_col.insert_one(item)).inserted_id


async def stock_find(query: dict, limit: int = 0, skip: int = 0) -> list[dict]:
    cur = stock_col.find(query).sort("added", 1).skip(skip)
    if limit:
        cur = cur.limit(limit)
    return [x async for x in cur]


async def stock_count(query: dict) -> int:
    return await stock_col.count_documents(query)


async def account_countries() -> list[dict]:
    """[{'_id': 'IN', 'name': 'India', 'n': 5}] for available accounts."""
    pipe = [{"$match": {"kind": "account", "status": "available"}},
            {"$group": {"_id": "$country", "name": {"$first": "$country_name"}, "n": {"$sum": 1}}},
            {"$sort": {"n": -1}}]
    return [x async for x in stock_col.aggregate(pipe)]


async def reserve_stock(query: dict, user_id: int):
    """Atomically take ONE available item -> reserved (no double selling)."""
    q = {**query, "status": "available"}
    return await stock_col.find_one_and_update(
        q, {"$set": {"status": "reserved", "reserved_by": user_id, "reserved_at": now()}},
        sort=[("added", 1)], return_document=ReturnDocument.AFTER)


async def release_stock(sid):
    await stock_col.update_one({"_id": sid, "status": "reserved"},
                               {"$set": {"status": "available"}, "$unset": {"reserved_by": "", "reserved_at": ""}})


async def get_stock(sid):
    return await stock_col.find_one({"_id": sid})


async def update_stock(sid, data: dict, unset: dict | None = None):
    upd = {"$set": data}
    if unset:
        upd["$unset"] = unset
    await stock_col.update_one({"_id": sid}, upd)


async def delete_stock(sid) -> bool:
    r = await stock_col.delete_one({"_id": sid, "status": "available"})
    return r.deleted_count == 1


# ---------------- orders
async def new_order(order: dict):
    order.update(created=now(), updated=now())
    order["_id"] = await next_order_no()
    await orders_col.insert_one(order)
    return order


async def next_order_no() -> int:
    doc = await counters_col.find_one_and_update({"_id": "order_no"}, {"$inc": {"n": 1}},
                                                 upsert=True, return_document=ReturnDocument.AFTER)
    return 1000 + doc["n"]


async def get_order(oid: int):
    return await orders_col.find_one({"_id": oid})


async def move_order(oid: int, frm: list[str] | str, to: str, extra: dict | None = None):
    """Atomic state change. Returns the updated order or None if it was not in `frm`
    (e.g. two owners pressed Approve at the same time)."""
    frm = [frm] if isinstance(frm, str) else frm
    return await orders_col.find_one_and_update(
        {"_id": oid, "status": {"$in": frm}},
        {"$set": {"status": to, "updated": now(), **(extra or {})}},
        return_document=ReturnDocument.AFTER)


async def set_order(oid: int, data: dict):
    await orders_col.update_one({"_id": oid}, {"$set": {**data, "updated": now()}})


async def user_orders(uid: int, limit: int = 10) -> list[dict]:
    return [x async for x in orders_col.find({"user_id": uid}).sort("_id", -1).limit(limit)]


async def orders_by_status(statuses: list[str], limit: int = 30) -> list[dict]:
    return [x async for x in orders_col.find({"status": {"$in": statuses}}).sort("_id", 1).limit(limit)]


async def stale_unpaid(before) -> list[dict]:
    return [x async for x in orders_col.find({"status": "awaiting_payment", "expires": {"$lt": before}})]


ACTIVE_ORDER = ["awaiting_payment", "awaiting_approval", "approved", "delivered",
                "transferring", "logging_out", "manual"]


async def sweep_orphans() -> int:
    """Stock stuck in 'reserved' without any live order goes back on sale (after a crash)."""
    n = 0
    for it in await stock_find({"status": "reserved"}):
        if not await orders_col.find_one({"stock_id": it["_id"], "status": {"$in": ACTIVE_ORDER}}):
            await release_stock(it["_id"])
            n += 1
    return n


async def sales_stats() -> dict:
    pipe = [{"$match": {"status": {"$in": ["delivered", "completed"]}}},
            {"$group": {"_id": None, "n": {"$sum": 1}, "sum": {"$sum": "$price"}}}]
    r = [x async for x in orders_col.aggregate(pipe)]
    return r[0] if r else {"n": 0, "sum": 0}


# ---------------- redeem codes
async def create_code(code: str, amount: float, uses: int, by: int):
    await codes_col.insert_one({"_id": code, "amount": amount, "uses": uses, "left": uses, "used_by": [],
                                "by": by, "created": now()})


async def redeem_code(code: str, uid: int):
    """Atomic: returns code doc on success; None if invalid / used up / already used by this user."""
    return await codes_col.find_one_and_update(
        {"_id": code, "used_by": {"$ne": uid}, "left": {"$gt": 0}},
        {"$push": {"used_by": uid}, "$inc": {"left": -1}}, return_document=ReturnDocument.AFTER)


async def get_code(code: str):
    return await codes_col.find_one({"_id": code})


async def list_codes(limit: int = 20) -> list[dict]:
    return [x async for x in codes_col.find({}).sort("created", -1).limit(limit)]


async def delete_code(code: str):
    await codes_col.delete_one({"_id": code})


# ---------------- seller accounts (own the channels / groups being sold)
async def save_seller(uid: int, session: str, info: dict, password: str | None):
    await sellers_col.update_one({"_id": uid}, {"$set": {"session": session, "info": info,
                                                         "password": password, "updated": now()}}, upsert=True)


async def get_seller(uid: int):
    return await sellers_col.find_one({"_id": uid})


async def all_sellers() -> list[dict]:
    return [x async for x in sellers_col.find({})]


async def remove_seller(uid: int):
    await sellers_col.delete_one({"_id": uid})
