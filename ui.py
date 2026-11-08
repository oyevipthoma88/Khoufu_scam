"""Shared button / message helpers (used by main.py and shop.py)."""
import logging
from copy import copy
from emoji_registry import leading_emoji, verified_id

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
    return _decorate(InlineKeyboardButton(text, url=url), UI["icons"])

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
    "t_normal": "✅ Normal Account", "t_scam": "⚠️ Scam Tag Account", "t_fake": "🎭 Fake Tag Account",
    "addstock": "➕ Add Stock", "stock": "📦 Stock List", "orders": "🧾 Orders", "price": "💰 Set Price",
    "qr": "🖼 Set QR", "upi": "🏦 Set UPI", "codes": "🎟 Redeem Codes", "force": "🔒 Must-Join",
    "menu": "🏠 Menu", "buynow": "💳 Buy now", "paid": "✅ I have paid", "otp": "📩 Get OTP",
    "approve": "✅ Approve", "reject": "❌ Reject", "addowner": "👑 Owners",
}
UI = {"emojis": {}, "icons": True, "start_pic": None}

# Premium icon for every button whose label starts with an emoji (flags too). 1000+ verified IDs.
# Owner Panel -> Button Emojis still overrides any of them.
from premium_emoji import PREMIUM  # noqa: E402


def _auto_icon(text: str):
    return leading_emoji(text)[1]


def _decorate(button, icons, override=None):
    button = copy(button)
    cluster, automatic = leading_emoji(button.text)
    eid = (verified_id(override) or verified_id(button.icon_custom_emoji_id) or automatic
           or PREMIUM["▫"]) if icons else None
    if eid and automatic:
        label = button.text[len(cluster):].lstrip(" ")
        if label:
            button.text = label
    button.icon_custom_emoji_id = eid
    return button


def B(text: str, data: str, style: ButtonStyle = D, key: str | None = None) -> tuple:
    return (text, data, style, key)


def build(rows, icons: bool | None = None) -> M:
    icons = UI["icons"] if icons is None else icons
    out = []
    for row in rows:
        r = []
        for b in row:
            if isinstance(b, InlineKeyboardButton):
                r.append(_decorate(b, icons))
                continue
            text, data, style, key = b
            if style == D:  # no plain grey buttons – every button gets a colour
                style = R if data and any(x in data for x in (":cx:", ":del", ":rj:", "clear", "picdel", "owrm")) else P
            r.append(_decorate(InlineKeyboardButton(text, style=style, callback_data=data),
                               icons, UI["emojis"].get(key)))
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
        if not UI["icons"] or not any(x in str(e).upper() for x in ("CUSTOM_EMOJI", "DOCUMENT_INVALID", "BUTTON_STYLE", "BUTTON_ICON")):
            raise
        log.warning("Button icons rejected (%s) – retrying this message without icons", e)
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

