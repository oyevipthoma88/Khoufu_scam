"""Regression tests for '/start error: Client has not been started yet'."""
import os
import subprocess
import sys
import textwrap

os.environ.setdefault("API_ID", "1"); os.environ.setdefault("API_HASH", "x")
os.environ.setdefault("BOT_TOKEN", "1:x"); os.environ.setdefault("MONGO_URI", "mongodb://localhost")
os.environ.setdefault("OWNER_IDS", "1")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from types import SimpleNamespace as NS
from unittest.mock import AsyncMock

import pytest
from pyrogram.errors import ChatAdminRequired, UserNotParticipant

import config
import shop_core as core
import shop_user


def test_is_owner_never_imports_main():
    sys.modules.pop("main", None)
    core.is_owner(424242)
    assert "main" not in sys.modules


def test_owner_added_in_bot_is_owner():
    config.EXTRA_OWNERS.append(777)
    try:
        assert core.is_owner(777) is True
        assert core.is_owner(778) is False
    finally:
        config.EXTRA_OWNERS.remove(777)


def test_bind_first_client_wins():
    old = core.BOT
    a, b = object(), object()
    try:
        core.BOT = None
        core.bind(a)
        core.bind(b)
        assert core.BOT is a
        core.bind(b, force=True)
        assert core.BOT is b
    finally:
        core.BOT = old


def test_running_main_then_import_main_keeps_one_bot():
    """`python main.py` + a later `import main` must not create a second Client."""
    script = textwrap.dedent("""
        import asyncio, runpy, sys
        asyncio.BaseEventLoop.run_until_complete = lambda self, coro: coro.close()
        ns = runpy.run_path("main.py", run_name="__main__")
        import main, shop_core, ui
        assert main.bot is ns["bot"], "import main created a new bot"
        assert shop_core.BOT is ns["bot"], "shop bound to a different bot"
        assert ui._bot is ns["bot"], "ui bound to a different bot"
        shop_core.is_owner(5)
        assert shop_core.BOT is ns["bot"]
        print("ONE_BOT_OK")
    """)
    r = subprocess.run([sys.executable, "-c", script], cwd=ROOT, capture_output=True, text=True,
                       env={**os.environ}, timeout=60)
    assert "ONE_BOT_OK" in r.stdout, r.stderr[-2000:]


@pytest.fixture
def gate(monkeypatch):
    chans = [{"chat": -1001, "title": "Apex", "link": "https://t.me/x"}]
    monkeypatch.setattr(shop_user, "_force_list", AsyncMock(return_value=chans))
    notify = AsyncMock()
    monkeypatch.setattr(shop_user, "notify_owners", notify)
    monkeypatch.setattr(shop_user, "lg", lambda *a, **k: None)
    shop_user._warned_force.clear()
    shop_user._ok_until.clear()
    old = core.BOT
    yield notify
    core.BOT = old


def _bot(side_effect, connected=True):
    return NS(get_chat_member=AsyncMock(side_effect=side_effect), is_connected=connected,
              start=AsyncMock())


async def test_gate_connection_error_lets_user_in_without_admin_warning(gate):
    core.BOT = _bot(ConnectionError("Client has not been started yet"), connected=False)
    assert await shop_user.missing_channels(1, use_cache=False) == []
    gate.assert_not_called()                 # owners are NOT told "make the bot admin"
    assert 1 not in shop_user._ok_until      # a skipped check is not cached as a pass


async def test_gate_admin_problem_warns_owners_once(gate):
    core.BOT = _bot(ChatAdminRequired())
    assert await shop_user.missing_channels(1, use_cache=False) == []
    assert await shop_user.missing_channels(2, use_cache=False) == []
    assert gate.await_count == 1


async def test_gate_not_joined_blocks(gate):
    core.BOT = _bot(UserNotParticipant())
    miss = await shop_user.missing_channels(1, use_cache=False)
    assert [c["chat"] for c in miss] == [-1001]


async def test_retry_reconnects_once_after_connection_error():
    old = core.BOT
    bot = NS(is_connected=False)

    async def start():
        bot.is_connected = True
    bot.start = start
    core.BOT = bot
    calls = []

    async def fn():
        calls.append(1)
        if len(calls) == 1:
            raise ConnectionError("Client has not been started yet")
        return "ok"
    try:
        assert await core.retry(fn) == "ok"
        assert len(calls) == 2
    finally:
        core.BOT = old
