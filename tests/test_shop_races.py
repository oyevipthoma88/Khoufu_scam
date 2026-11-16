"""Shop safety: one owner decides a sale; fresh checkouts are never swept back on sale."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
for k, v in {"API_ID": "1", "API_HASH": "x", "BOT_TOKEN": "1:x", "MONGO_URI": "mongodb://localhost", "OWNER_IDS": "1"}.items():
    os.environ.setdefault(k, v)
import datetime as dt
import pytest
from mongomock_motor import AsyncMongoMockClient
import database as db


@pytest.fixture(autouse=True)
def mockdb(monkeypatch):
    m = AsyncMongoMockClient()["t"]
    for name in ("settings", "accounts", "counters", "users", "stock", "orders", "codes", "sellers"):
        monkeypatch.setattr(db, f"{name}_col", m[name])
    yield


async def test_sale_can_be_claimed_only_once():
    await db.orders_col.insert_one({"_id": 1, "status": "delivered"})
    first = await db.claim_sale(1, {"sale_ok": True})
    second = await db.claim_sale(1, {"sale_flag": True, "status": "declined"})
    assert first is not None and second is None
    o = await db.get_order(1)
    assert o["sale_ok"] is True and o["status"] == "delivered"


async def test_fresh_reservation_without_order_is_not_released():
    await db.stock_col.insert_one({"_id": "a", "status": "reserved", "reserved_at": db.now()})
    assert await db.sweep_orphans() == 0
    assert (await db.get_stock("a"))["status"] == "reserved"


async def test_old_reservation_without_order_is_released():
    old = db.now() - dt.timedelta(minutes=11)
    await db.stock_col.insert_one({"_id": "b", "status": "reserved", "reserved_at": old})
    assert await db.sweep_orphans() == 1
    assert (await db.get_stock("b"))["status"] == "available"
