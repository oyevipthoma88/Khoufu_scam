"""Run: pip install pytest pytest-asyncio mongomock-motor && pytest -q"""
import os
import sys

os.environ.setdefault("API_ID", "1"); os.environ.setdefault("API_HASH", "x")
os.environ.setdefault("BOT_TOKEN", "1:x"); os.environ.setdefault("MONGO_URI", "mongodb://localhost")
os.environ.setdefault("OWNER_IDS", "1")
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import pytest
from mongomock_motor import AsyncMongoMockClient

import database as db
import shop_tg
from shop_core import price_of


@pytest.fixture(autouse=True)
def mockdb(monkeypatch):
    m = AsyncMongoMockClient()["t"]
    for name in ("settings", "accounts", "counters", "users", "stock", "orders", "codes", "sellers"):
        monkeypatch.setattr(db, f"{name}_col", m[name])
    yield


def test_otp_extracted_and_spaced():
    code = shop_tg.extract_code("Login code: 54321. Do not give this code to anyone")
    assert code == "54321"
    assert shop_tg.spaced(code) == "5 4 3 2 1"
    assert shop_tg.extract_code("New login from Chrome on 03/11/2026") is None


def test_country_from_phone():
    assert shop_tg.country_of("919876543210") == ("IN", "India")
    assert shop_tg.country_of("") == ("XX", "Unknown")


def test_chat_links_parsed():
    assert shop_tg.parse_chat_ref("https://t.me/mychannel") == "mychannel"
    assert shop_tg.parse_chat_ref("t.me/+AbCdEf123") == "https://t.me/+AbCdEf123"
    assert shop_tg.parse_chat_ref("https://t.me/c/123456789/5") == -100123456789
    assert shop_tg.parse_chat_ref("hello world") is None


def test_price_priority_item_then_country_then_default():
    shop = {"prices": {"account": {"_": 100, "IN": 80}, "channel": 50}}
    assert price_of(shop, {"kind": "account", "country": "IN"}) == 80
    assert price_of(shop, {"kind": "account", "country": "US"}) == 100
    assert price_of(shop, {"kind": "channel"}) == 50
    assert price_of(shop, {"kind": "channel", "price": 70}) == 70
    assert price_of(shop, {"kind": "bot"}) == 0  # not set -> can't be sold


async def test_same_item_cannot_be_sold_twice():
    await db.add_stock({"kind": "account", "country": "IN"})
    first = await db.reserve_stock({"kind": "account", "country": "IN"}, 10)
    second = await db.reserve_stock({"kind": "account", "country": "IN"}, 11)
    assert first is not None and second is None


async def test_approve_only_once():
    o = await db.new_order({"user_id": 5, "status": "awaiting_approval", "price": 10})
    assert await db.move_order(o["_id"], ["awaiting_approval"], "approved") is not None
    assert await db.move_order(o["_id"], ["awaiting_approval"], "approved") is None


async def test_redeem_code_once_per_user_and_limited_uses():
    await db.create_code("ABCD", 50, 1, 1)
    assert await db.redeem_code("ABCD", 7) is not None
    assert await db.redeem_code("ABCD", 7) is None  # same user again
    assert await db.redeem_code("ABCD", 8) is None  # uses exhausted


async def test_balance_never_negative():
    await db.users_col.insert_one({"_id": 3, "balance": 40})
    assert await db.take_balance(3, 50) is False
    assert await db.take_balance(3, 40) is True
    assert (await db.get_user(3))["balance"] == 0


def test_scam_and_normal_same_country_have_separate_prices():
    from shop_core import acc_key, price_for_country
    shop = {"prices": {"account": {acc_key("normal", "IN"): 80, acc_key("scam", "IN"): 30}}}
    assert price_for_country(shop, "IN", "normal") == 80
    assert price_for_country(shop, "IN", "scam") == 30
    assert price_of(shop, {"kind": "account", "country": "IN", "tag": "scam"}) == 30


def test_scam_never_uses_normal_price():
    shop = {"prices": {"account": {"IN": 80, "_": 100}}}
    assert price_of(shop, {"kind": "account", "country": "IN", "tag": "scam"}) == 0
    assert price_of(shop, {"kind": "account", "country": "IN"}) == 80  # old untagged stock = normal


def test_tag_default_price():
    from shop_core import acc_key
    shop = {"prices": {"account": {acc_key("fake", "_"): 40}}}
    assert price_of(shop, {"kind": "account", "country": "US", "tag": "fake"}) == 40
    assert price_of(shop, {"kind": "account", "country": "US", "tag": "normal"}) == 0


def test_log_entry_format():
    import logchan
    e = logchan.fmt_entry("Order #5 created | buyer: 123 | <x>", "🛒", stamp="10:00:00")
    assert "<b>Order #5 created</b>" in e and "#order" in e
    assert "<code>123</code>" in e and "&lt;x&gt;" in e


def test_every_button_emoji_has_premium_icon():
    import glob, re
    from premium_emoji import PREMIUM
    from ui import _auto_icon
    missing = set()
    for f in glob.glob(os.path.join(os.path.dirname(os.path.dirname(__file__)), "*.py")):
        for lab in re.findall(r'B\("([^"{]+)"', open(f, encoding="utf-8").read()):
            head = lab.split(" ", 1)[0]
            if head and not head[0].isalnum() and not _auto_icon(lab):
                missing.add(head)
    assert not missing, missing
    assert _auto_icon("🇮🇳 India • 5") == PREMIUM["🇮🇳"]  # country buttons get flag icons
    assert all(v.isdigit() for v in PREMIUM.values())


def test_premium_emojis_in_text():
    from premium_text import emoji_entities
    from premium_emoji import PREMIUM
    ents = emoji_entities("✅ Paid 💰 ₹99")
    assert [e.offset for e in ents] == [0, 7]
    assert ents[1].document_id == int(PREMIUM["💰"])
    assert emoji_entities("⚠️ alert")[0].length == 2  # variation selector included


def test_code_blocks_keep_plain_emoji():
    from pyrogram import raw
    from premium_text import emoji_entities
    code = raw.types.MessageEntityCode(offset=0, length=2)
    assert emoji_entities("🔥 hot 🔥", [code])[0].offset == 7


def test_bot_parser_adds_premium_only_for_bot():
    import asyncio
    from pyrogram.parser.parser import Parser
    import premium_text
    premium_text.install()
    class C: bot_token = "1:x"; parse_mode = None
    class U: bot_token = None; parse_mode = None
    from pyrogram import enums
    C.parse_mode = U.parse_mode = enums.ParseMode.DEFAULT
    r = asyncio.run(Parser(C()).parse("**🎉 Done**"))
    assert any(type(e).__name__ == "MessageEntityCustomEmoji" for e in r["entities"])
    r2 = asyncio.run(Parser(U()).parse("**🎉 Done**"))
    assert not any(type(e).__name__ == "MessageEntityCustomEmoji" for e in r2["entities"] or [])
