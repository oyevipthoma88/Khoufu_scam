"""Premium button icon engine: Telegram only shows icons when the bot's BotFather owner has Premium."""
import asyncio, os, sys, time
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
for k, v in {"API_ID": "1", "API_HASH": "x", "BOT_TOKEN": "1:x", "MONGO_URI": "mongodb://localhost", "OWNER_IDS": "1"}.items():
    os.environ.setdefault(k, v)
from types import SimpleNamespace as NS

import pytest
from pyrogram.enums import ChatType
from pyrogram.errors import BadRequest, ButtonDataInvalid

import ui
from ui import B, G, U

ROWS = [[B("➕ Add Account", "add", G), U("📢 Updates", "https://t.me/x")]]


@pytest.fixture(autouse=True)
def fresh_state():
    ui.UI["icons"] = True
    ui.ICON_STATUS.update(state="unknown", reason="", since=0.0, retry_at=0.0, checked=0.0)
    yield
    ui.ICON_STATUS.update(state="unknown", reason="", since=0.0, retry_at=0.0, checked=0.0)


class Echo:
    """Fake sent message: Telegram echoes the keyboard, with or without our icons."""
    def __init__(self, markup, keep_icons, chat_type=ChatType.PRIVATE):
        if not keep_icons:
            for row in markup.inline_keyboard:
                for b in row:
                    b.icon_custom_emoji_id = None
        self.reply_markup, self.chat, self.repaired = markup, NS(type=chat_type), None

    async def edit_reply_markup(self, markup):
        self.repaired = markup


def sender(keep_icons, chat_type=ChatType.PRIVATE, store=None):
    async def go(ic):
        msg = Echo(ui.build(ROWS, ic), keep_icons, chat_type)
        if store is not None:
            store.append(msg)
        return msg
    return lambda ic: go(ic)


def labels(markup):
    return [b.text for row in markup.inline_keyboard for b in row]


def test_echoed_icons_mean_live():
    asyncio.run(ui.safe_send(sender(True)))
    assert ui.ICON_STATUS["state"] == "active"
    assert "LIVE" in ui.icon_status_line()


def test_stripped_icons_mean_blocked_and_message_gets_normal_emojis_back():
    sent = []
    asyncio.run(ui.safe_send(sender(False, store=sent)))
    assert ui.ICON_STATUS["state"] == "dropped"
    assert "BLOCKED" in ui.icon_status_line()
    assert labels(sent[0].repaired) == ["➕ Add Account", "📢 Updates"]


def test_while_blocked_buttons_keep_normal_emojis_and_no_icons():
    asyncio.run(ui.safe_send(sender(False)))
    markup = ui.build(ROWS)
    assert labels(markup) == ["➕ Add Account", "📢 Updates"]
    assert not any(b.icon_custom_emoji_id for row in markup.inline_keyboard for b in row)


def test_icons_are_retested_after_probe_interval_and_come_back():
    asyncio.run(ui.safe_send(sender(False)))
    assert ui.icons_wanted() is False
    ui.ICON_STATUS["retry_at"] = time.time() - 1  # interval passed
    assert ui.icons_wanted() is True
    asyncio.run(ui.safe_send(sender(True)))  # owner bought Premium meanwhile
    assert ui.ICON_STATUS["state"] == "active"
    assert labels(ui.build(ROWS)) == ["Add Account", "Updates"]  # emoji moved into the premium icon


def test_owner_test_button_forces_icons_on_next_message():
    asyncio.run(ui.safe_send(sender(False)))
    ui.force_icon_test()
    assert ui.icons_wanted() is True


def test_channel_result_never_changes_the_state():
    asyncio.run(ui.safe_send(sender(False, ChatType.CHANNEL)))
    assert ui.ICON_STATUS["state"] == "unknown"


def test_rejected_only_when_message_works_without_icons():
    calls = []

    async def go(ic):
        markup = ui.build(ROWS, ic)
        calls.append(ic)
        if ic is None:
            raise BadRequest("PREMIUM_ACCOUNT_REQUIRED")
        return Echo(markup, False)

    res = asyncio.run(ui.safe_send(lambda ic: go(ic)))
    assert calls == [None, False]
    assert ui.ICON_STATUS["state"] == "rejected"
    assert labels(res.reply_markup) == ["➕ Add Account", "📢 Updates"]


def test_other_errors_are_not_blamed_on_icons():
    async def go(ic):
        ui.build(ROWS, ic)
        raise ButtonDataInvalid()

    with pytest.raises(ButtonDataInvalid):
        asyncio.run(ui.safe_send(lambda ic: go(ic)))
    assert ui.ICON_STATUS["state"] == "unknown"


def test_icons_switched_off_by_owner():
    ui.UI["icons"] = False
    assert ui.icons_wanted() is False
    assert "OFF" in ui.icon_status_line()
