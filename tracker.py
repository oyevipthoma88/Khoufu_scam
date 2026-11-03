"""A-to-Z activity logs + smart extras (ban, broadcast, stats, quick commands).

Runs in handler groups BEFORE the shop / owner panel:
  -3  extra commands (they stop propagation after answering)
  -4  activity tracker + ban gate (never stops normal updates)
"""
import asyncio
import time

from pyrogram import StopPropagation, filters
from pyrogram.errors import FloodWait, InputUserDeactivated, UserIsBlocked
from pyrogram.handlers import CallbackQueryHandler, MessageHandler

import config
import database as db
import shop_core as core
from shop_core import B, G, P, clean, lg, money, who

BANNED: set[int] = set()
_seen_today: dict[int, str] = {}


def _owner(uid: int) -> bool:
    return uid in config.OWNER_IDS


def _btn_label(q) -> str:
    """Find the text of the button that was pressed."""
    try:
        for row in q.message.reply_markup.inline_keyboard:
            for b in row:
                if b.callback_data == q.data:
                    return b.text
    except Exception:
        pass
    return "?"


def _role(uid: int) -> str:
    return "👑OWNER" if _owner(uid) else "👤USER"


# ------------------------------------------------------------ tracker (group -2)
async def _track_msg(_, m):
    u = m.from_user
    if not u:
        return
    if u.id in BANNED and not _owner(u.id):
        raise StopPropagation
    if m.text and m.text.startswith("/"):
        cmd = m.text.split()[0]
        doc = await db.get_user(u.id)
        if cmd.startswith("/start"):
            new = not doc
            ref = m.text.split(maxsplit=1)[1] if " " in m.text else "-"
            lg(f"{'🆕 NEW USER' if new else '▶️ START'} {_role(u.id)} {who(u)} | lang {u.language_code or '-'} | "
               f"premium {'yes' if u.is_premium else 'no'} | ref {ref}", "🚀")
        else:
            lg(f"COMMAND {cmd} by {_role(u.id)} {who(u)}", "⌨️")
        return
    kind = ("photo" if m.photo else "document" if m.document else "sticker" if m.sticker
            else "voice" if m.voice else "video" if m.video else "text")
    step = (core.SSTATE.get(u.id) or {}).get("step", "-")
    preview = clean(m.text or m.caption or "")[:60]
    if step in ("l2fa", "lcode", "lstring", "string", "code", "bot_token", "phone", "lphone") or any(x in step for x in ("2fa", "pw", "pass")):  # never log secrets
        preview = "•••hidden•••"
    lg(f"MESSAGE ({kind}) by {_role(u.id)} {who(u)} | step {step} | {preview}", "💬")


async def _track_cb(_, q):
    u = q.from_user
    if u.id in BANNED and not _owner(u.id):
        try:
            await q.answer("🚫 You are banned from this bot.", show_alert=True)
        except Exception:
            pass
        raise StopPropagation
    lg(f"BUTTON [{clean(_btn_label(q))}] ({q.data}) by {_role(u.id)} {who(u)}", "🔘")


# ------------------------------------------------------------ extra commands (group -3)
async def _help(_, m):
    owner = _owner(m.from_user.id)
    txt = ("✨ **Quick commands**\n━━━━━━━━━━━━━━━━━━\n"
           "/start – open the store\n/balance – your wallet\n/orders – your recent orders\n"
           "/help – this message\n/cancel – stop current step")
    if owner:
        txt += ("\n\n👑 **Owner**\n/stats – full shop stats\n/user `id` – user details\n"
                "/ban `id` • /unban `id`\n/broadcast – reply to any message to send it to all users")
    await core.reply(m, txt, [core.home_btn(owner)])
    raise StopPropagation


async def _balance(_, m):
    u = await db.get_user(m.from_user.id) or {}
    await core.reply(m, f"💰 **Your wallet:** {money(u.get('balance', 0))}\n🎟 Add balance with a redeem code.",
                     [[B("🎟 Redeem Code", "u:redeem", P), B("🏠 Menu", "u:home", G)]])
    raise StopPropagation


async def _orders(_, m):
    await core.reply(m, "🧾 Tap below to see your orders 👇", [[B("🧾 My Orders", "u:orders", P)]])
    raise StopPropagation


async def _stats(_, m):
    users = await db.count_users()
    sales = await db.sales_stats()
    stock = await db.stock_count({"status": "available"})
    pending = await db.orders_col.count_documents({"status": "awaiting_approval"})
    day = db.now().replace(hour=0, minute=0, second=0, microsecond=0)
    today_users = await db.users_col.count_documents({"joined": {"$gte": day}})
    active = await db.users_col.count_documents({"seen": {"$gte": day}})
    lines = ["📊 **Shop stats**\n━━━━━━━━━━━━━━━━━━",
             f"👥 Users: **{users}** (new today {today_users}, active today {active})",
             f"🚫 Banned: **{len(BANNED)}**",
             f"💸 Sales: **{sales['n']}** • Revenue **{money(sales['sum'])}**",
             f"⏳ Waiting approval: **{pending}**", f"📦 In stock: **{stock}**"]
    for k, name in core.KIND_NAME.items():
        n = await db.stock_count({"kind": k, "status": "available"})
        lines.append(f"   {core.KIND_ICON[k]} {name}: {n}")
    await core.reply(m, "\n".join(lines))
    raise StopPropagation


def _arg_id(m):
    p = (m.text or "").split()
    return int(p[1]) if len(p) > 1 and p[1].lstrip("-").isdigit() else None


async def _user(_, m):
    uid = _arg_id(m)
    u = await db.get_user(uid) if uid else None
    if not u:
        await m.reply("Usage: /user `id` (user must have started the bot)")
        raise StopPropagation
    n = await db.orders_col.count_documents({"user_id": uid})
    done = await db.orders_col.count_documents({"user_id": uid, "status": "completed"})
    await m.reply(f"👤 **User** `{uid}`\nName: {clean(u.get('name'))}\nUsername: @{u.get('username') or '-'}\n"
                  f"💰 Balance: {money(u.get('balance', 0))}\n🧾 Orders: {n} (completed {done})\n"
                  f"📅 Joined: {u.get('joined')}\n👀 Last seen: {u.get('seen')}\n"
                  f"🚫 Banned: {'yes' if uid in BANNED else 'no'}")
    raise StopPropagation


async def _ban(_, m, on: bool):
    uid = _arg_id(m)
    if not uid or _owner(uid):
        await m.reply("Usage: /ban `id` or /unban `id` (owners can't be banned)")
        raise StopPropagation
    (BANNED.add if on else BANNED.discard)(uid)
    await db.users_col.update_one({"_id": uid}, {"$set": {"banned": on}}, upsert=True)
    lg(f"{'BANNED' if on else 'UNBANNED'} {uid} by {who(m.from_user)}", "🚫" if on else "✅")
    await m.reply(f"{'🚫 Banned' if on else '✅ Unbanned'} `{uid}`")
    raise StopPropagation


async def _ban_on(c, m):
    await _ban(c, m, True)


async def _ban_off(c, m):
    await _ban(c, m, False)


async def _broadcast(c, m):
    src = m.reply_to_message
    if not src:
        await m.reply("↩️ Reply to the message you want to send to all users with /broadcast")
        raise StopPropagation
    status = await m.reply("📣 Broadcasting…")
    ok = fail = blocked = 0
    async for u in db.users_col.find({"banned": {"$ne": True}}, {"_id": 1}):
        try:
            await src.copy(u["_id"])
            ok += 1
        except FloodWait as e:
            await asyncio.sleep(int(e.value or 3) + 1)
            try:
                await src.copy(u["_id"]); ok += 1
            except Exception:
                fail += 1
        except (UserIsBlocked, InputUserDeactivated):
            blocked += 1
        except Exception:
            fail += 1
        await asyncio.sleep(0.05)  # ~20 msg/s, under Telegram limits
    lg(f"BROADCAST by {who(m.from_user)}: sent {ok}, blocked {blocked}, failed {fail}", "📣")
    await status.edit_text(f"📣 **Broadcast done**\n✅ Sent: {ok}\n🚫 Blocked/deleted: {blocked}\n❌ Failed: {fail}")
    raise StopPropagation


async def load_bans():
    BANNED.update([u["_id"] async for u in db.users_col.find({"banned": True}, {"_id": 1})])


def register(bot, owner_filter):
    p = filters.private
    bot.add_handler(MessageHandler(_track_msg, p), -4)
    bot.add_handler(CallbackQueryHandler(_track_cb), -4)
    g = -3
    bot.add_handler(MessageHandler(_help, p & filters.command("help")), g)
    bot.add_handler(MessageHandler(_balance, p & filters.command("balance")), g)
    bot.add_handler(MessageHandler(_orders, p & filters.command("orders")), g)
    bot.add_handler(MessageHandler(_stats, p & owner_filter & filters.command("stats")), g)
    bot.add_handler(MessageHandler(_user, p & owner_filter & filters.command("user")), g)
    bot.add_handler(MessageHandler(_ban_on, p & owner_filter & filters.command("ban")), g)
    bot.add_handler(MessageHandler(_ban_off, p & owner_filter & filters.command("unban")), g)
    bot.add_handler(MessageHandler(_broadcast, p & owner_filter & filters.command("broadcast")), g)
