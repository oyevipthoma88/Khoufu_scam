import pytest
import asyncio
import base64
import struct
import userbot
import config
from unittest.mock import AsyncMock, MagicMock

def test_telethon_to_pyrogram_unpadded():
    # Test synthetic Telethon IPv4 session (unpadded vs padded)
    raw_ip = b"\x01\x02\x03\x04"
    key = b"K" * 256
    data = struct.pack(">B4sH256s", 2, raw_ip, 443, key)
    raw_b64 = base64.urlsafe_b64encode(data).decode().rstrip("=")
    unpadded = "1" + raw_b64
    padded = unpadded + "="
    
    res1, fix1 = userbot.telethon_to_pyrogram(unpadded)
    res2, fix2 = userbot.telethon_to_pyrogram(padded)
    res3, fix3 = userbot.telethon_to_pyrogram(f"'{unpadded}'")
    
    assert fix1 is True
    assert fix2 is True
    assert fix3 is True
    assert res1 == res2 == res3

def test_proof_media_id_document():
    import shop_user
    m = MagicMock()
    m.photo = None
    m.document = MagicMock()
    m.document.file_id = "doc_file_123"
    m.document.mime_type = "image/png"
    m.document.file_name = "screenshot.png"
    assert shop_user._proof_media_id(m) == "doc_file_123"

    m.document.mime_type = "application/octet-stream"
    m.document.file_name = "payment_receipt.jpg"
    assert shop_user._proof_media_id(m) == "doc_file_123"

@pytest.mark.asyncio
async def test_sale_confirmed_approval_and_decline(monkeypatch):
    import shop_user
    import database as db
    import shop_core as core

    order_store = {
        101: {"_id": 101, "user_id": 999, "stock_id": 55, "status": "delivered", "kind": "account", "price": 100}
    }
    stock_store = {
        55: {"_id": 55, "phone": "1234567890", "status": "reserved", "added_by": 111}
    }

    async def mock_get_order(oid):
        return order_store.get(oid)

    async def mock_set_order(oid, fields):
        if oid in order_store:
            order_store[oid].update(fields)

    async def mock_get_stock(sid):
        return stock_store.get(sid)

    async def mock_update_stock(sid, fields):
        if sid in stock_store:
            stock_store[sid].update(fields)

    async def mock_release_stock(sid):
        if sid in stock_store:
            stock_store[sid]["status"] = "available"

    monkeypatch.setattr(db, "get_order", mock_get_order)
    monkeypatch.setattr(db, "set_order", mock_set_order)
    monkeypatch.setattr(db, "get_stock", mock_get_stock)
    monkeypatch.setattr(db, "update_stock", mock_update_stock)
    monkeypatch.setattr(db, "release_stock", mock_release_stock)
    monkeypatch.setattr(core, "is_owner", lambda uid: True)
    monkeypatch.setattr(shop_user, "show", AsyncMock())
    monkeypatch.setattr(shop_user, "send", AsyncMock())
    monkeypatch.setattr(shop_user, "notify_owners", AsyncMock())

    # Approve
    q_ok = MagicMock()
    q_ok.from_user.id = 111
    q_ok.from_user.first_name = "Owner1"
    q_ok.from_user.last_name = None
    q_ok.from_user.username = "owner1"
    q_ok.answer = AsyncMock()

    await shop_user.sale_confirmed(q_ok, 101, ok=True)
    assert order_store[101]["sale_ok"] is True
    assert stock_store[55]["status"] == "sold"

    # Reset for decline test
    order_store[102] = {"_id": 102, "user_id": 999, "stock_id": 56, "status": "delivered", "kind": "account", "price": 100}
    stock_store[56] = {"_id": 56, "phone": "9876543210", "status": "reserved", "added_by": 111}

    q_no = MagicMock()
    q_no.from_user.id = 111
    q_no.from_user.first_name = "Owner1"
    q_no.from_user.last_name = None
    q_no.from_user.username = "owner1"
    q_no.answer = AsyncMock()

    await shop_user.sale_confirmed(q_no, 102, ok=False)
    assert order_store[102]["status"] == "declined"
    assert stock_store[56]["status"] == "available"
