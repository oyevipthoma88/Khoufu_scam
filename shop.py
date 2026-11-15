"""Shop entry point: registers handlers (group -1, before the owner panel) and background jobs."""
import asyncio
import time

from pyrogram import StopPropagation, filters
from pyrogram.handlers import CallbackQueryHandler, MessageHandler

import config
import database as db
import shop_admin
import shop_core as core
import shop_user
import tracker
from shop_core import SSTATE, B, D, friendly_err, lg

clear_state = core.clear_state
OWNER = filters.user(config.OWNER_IDS)


async def _has_state(_, __, m):
    return bool(m.from_user) and m.from_user.id in SSTATE

HAS_STATE = filters.create(_has_state)


async def _nocmd(_, __, m):
    return not ((m.text or m.caption or "").startswith("/"))


NOCMD = filters.create(_nocmd)


async def _cb(_, q):
    try:
        if q.data.startswith("so:"):
            await shop_admin.admin_cb(q)
        else:
            await shop_user.user_cb(q)
    except StopPropagation:
        raise
    except Exception as e:
        core.log.exception("shop callback %s failed", q.data)
        lg(f"Button '{q.data}' by {q.from_user.id} error: {friendly_err(e)}", "❌")
        try:
            await core.send(q.message.chat.id, f"⚠️ **Something went wrong:** {friendly_err(e)}\nPlease try again.",
                            [core.home_btn(q.data.startswith("so:"))])
        except Exception:
            pass
    raise StopPropagation


async def _start(c, m):
    try:
        await shop_user.cmd_start(c, m)
    except Exception as e:
        core.log.exception("start failed")
        lg(f"/start error for {m.from_user.id}: {friendly_err(e)}", "❌")
        try:
            await core.retry(m.reply, "⚠️ Something went wrong, please send /start again.")
        except Exception:
            pass
    raise StopPropagation


async def _cancel(_, m):
    await clear_state(m.from_user.id)
    await core.reply(m, "❌ Cancelled.", [core.home_btn(core.is_owner(m.from_user.id))])
    raise StopPropagation


async def _input(_, m):
    uid = m.from_user.id
    st = SSTATE.get(uid)
    if st:
        if "t" not in st:
            st["t"] = time.time()
        elif time.time() - st["t"] > 1800:
            await clear_state(uid)
            SSTATE.pop(uid, None)
            st = None
            try:
                await core.reply(m, "⌛ Session expired – start again from the menu.",
                                 [core.home_btn(core.is_owner(uid))])
            except Exception:
                pass
            raise StopPropagation
    try:
        if st:
            if core.is_owner(uid) and st["step"] not in ("proof", "redeem", "target"):
                await shop_admin.admin_input(m, st)
            else:
                await shop_user.user_input(m, st)
    except Exception as e:
        core.log.exception("shop input failed")
        lg(f"Input error ({st and st.get('step')}) by {uid}: {friendly_err(e)}", "❌")
        await clear_state(uid)
        try:
            await core.reply(m, f"⚠️ **Error:** {friendly_err(e)}\nNothing was saved – please try again.",
                             [core.home_btn(core.is_owner(uid))])
        except Exception:
            pass
    raise StopPropagation


async def _fallback(_, m):
    if core.too_fast(m.from_user.id, 1.5):  # anti-spam: ignore message floods
        raise StopPropagation
    # proof sent after a restart / lost session must still reach the owner
    if await shop_user.route_pending_proof(m):
        raise StopPropagation
    await core.reply(m, "Use the menu 👇", [[B("🏠 Open Menu", "u:home", D)]])
    raise StopPropagation


def register(bot):
    core.bind(bot)
    tracker.register(bot, OWNER)
    g = -1  # runs before the owner panel handlers in main.py
    bot.add_handler(CallbackQueryHandler(_cb, filters.regex(r"^(u|so):")), g)
    bot.add_handler(MessageHandler(_start, filters.private & ~OWNER & filters.command(["start", "menu"])), g)
    bot.add_handler(MessageHandler(_cancel, filters.private & filters.command("cancel") & (~OWNER | HAS_STATE)), g)
    bot.add_handler(MessageHandler(_input, filters.private & HAS_STATE & NOCMD), g)
    bot.add_handler(MessageHandler(_fallback, filters.private & ~OWNER), g)


async def _expire_logins():
    while True:
        await asyncio.sleep(60)
        for uid, st in list(SSTATE.items()):
            if st.get("client") and time.time() - st.get("t", time.time()) > 600:
                await clear_state(uid)
                try:
                    await core.send(uid, "⌛ Login timed out. Start again.")
                except Exception:
                    pass


async def _boot():
    try:
        await db.ensure_indexes()
    except Exception as e:
        lg(f"Index creation failed: {friendly_err(e)}", "⚠️")
    try:
        await tracker.load_bans()
    except Exception as e:
        lg(f"Ban list load failed: {friendly_err(e)}", "⚠️")
    await shop_user.reaper()


def start_background():
    asyncio.create_task(_boot())
    asyncio.create_task(_expire_logins())
