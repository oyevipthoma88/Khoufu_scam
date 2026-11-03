"""Shared button / message helpers (used by main.py and shop.py)."""
import logging

from pyrogram.enums import ButtonStyle
from pyrogram.errors import BadRequest, FloodWait, MessageNotModified
from pyrogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup as M, Message

import database as db
from logchan import NOPREV

log = logging.getLogger("ui")
_bot = None


def bind(bot):
    global _bot
    _bot = bot


def U(text: str, url: str) -> InlineKeyboardButton:
    """URL button."""
    return InlineKeyboardButton(text, url=url)

# ------------------------------------------------------------ buttons (colour + premium emoji)
# Each button spec: (text, callback_data, style, emoji_key)
G, R, P, D = ButtonStyle.SUCCESS, ButtonStyle.DANGER, ButtonStyle.PRIMARY, ButtonStyle.DEFAULT
EMOJI_KEYS = {
    "add": "➕ Add Account", "accs": "👤 My Accounts", "custom": "⚙️ Custom", "tut": "📖 Tutorial",
    "apply": "🚀 Apply", "auto": "🤖 Auto", "links": "🔗 Links", "status": "📊 Status",
    "back": "🔙 Back", "yes": "✅ Yes", "no": "❌ No", "reset": "♻️ Reset", "remove": "🗑 Remove",
    "owner": "👑 Owner Panel", "edit": "✏️ Edit fields",
    "shopadm": "🛍 Shop Admin", "shopusr": "🛒 User View",
    "u_acc": "📱 Buy Account", "u_chan": "📢 Buy Channel", "u_grp": "👥 Buy Group", "u_bot": "🤖 Buy Bot",
    "u_meth": "📚 Buy Method", "u_owner": "👑 Owner", "u_upd": "📣 Update Channel", "u_tut": "📖 Tutorial",
    "u_redeem": "🎟 Redeem Code", "u_orders": "🧾 My Orders",
}
UI = {"emojis": {}, "icons": True, "start_pic": None}


def B(text: str, data: str, style: ButtonStyle = D, key: str | None = None) -> tuple:
    return (text, data, style, key)


def build(rows, icons: bool | None = None) -> M:
    icons = UI["icons"] if icons is None else icons
    out = []
    for row in rows:
        r = []
        for b in row:
            if isinstance(b, InlineKeyboardButton):
                r.append(b)
                continue
            text, data, style, key = b
            eid = UI["emojis"].get(key) if (icons and key) else None
            if eid is not None and not str(eid).isdigit():
                eid = None  # broken/old value in DB (e.g. 'add') – ignore instead of crashing
            if eid:
                # premium icon replaces the plain emoji at the start of the label
                text = text.split(" ", 1)[1] if " " in text and not text[0].isalnum() else text
            r.append(InlineKeyboardButton(text, icon_custom_emoji_id=str(eid) if eid else None,
                                          style=style, callback_data=data))
        out.append(r)
    return M(out)


BACK = [B("🔙 Back", "home", D, "back")]


def back(to: str) -> list:
    return [B("🔙 Back", to, D, "back")]


async def safe_send(coro_factory):
    """Send with premium icons; if Telegram rejects them, retry once without and remember."""
    try:
        return await coro_factory(None)
    except MessageNotModified:
        return None
    except (BadRequest, ValueError) as e:
        if not UI["icons"] or not UI["emojis"]:
            raise
        log.warning("Button icons rejected (%s) – disabling icons", e)
        UI["icons"] = False
        await db.set_ui("icons", False)
        return await coro_factory(False)


async def reply(m: Message, text: str, rows=None):
    return await safe_send(lambda ic: m.reply(text, reply_markup=build(rows, ic) if rows else None,
                                              link_preview_options=NOPREV))


async def send(chat_id: int, text: str, rows=None):
    return await safe_send(lambda ic: _bot.send_message(chat_id, text, reply_markup=build(rows, ic) if rows else None,
                                                       link_preview_options=NOPREV))


async def edit(q: CallbackQuery, text: str, rows):
    msg = q.message
    if msg.photo:  # can't turn a photo message into text – send a fresh one
        if len(text) <= 1000:
            try:
                return await safe_send(lambda ic: msg.edit_caption(text, reply_markup=build(rows, ic) if rows else None))
            except Exception:
                pass
        try:
            await msg.delete()
        except Exception:
            pass
        return await send(msg.chat.id, text, rows)
    try:
        return await safe_send(lambda ic: msg.edit_text(text, reply_markup=build(rows, ic) if rows else None,
                                                        link_preview_options=NOPREV))
    except Exception:
        return await send(msg.chat.id, text, rows)

