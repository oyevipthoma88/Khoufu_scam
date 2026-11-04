"""Premium emojis inside message TEXT (not only buttons).

install() patches pyrogram once, so EVERY message / caption / edit the BOT sends
(menus, orders, logs, alerts …) gets its plain emojis turned into matching premium
emojis automatically – no change needed at the 100+ places that send text.

Safe:
- only the bot client is touched (seller / user accounts are never changed)
- emojis inside `code` / ```pre``` blocks are left alone
- max 100 premium emojis per message (Telegram limit)
- if Telegram ever refuses them, the message is re-sent with plain emojis and the
  feature switches itself off (set PREMIUM_TEXT=0 to turn it off yourself)
"""
import logging
import os
import re

from pyrogram import Client, raw
from pyrogram.errors import BadRequest
from pyrogram.parser.parser import Parser

from premium_emoji import PREMIUM

log = logging.getLogger("premium_text")
STATE = {"on": os.environ.get("PREMIUM_TEXT", "1") != "0"}
MAX_PER_MSG = 100

# longest first so "❤‍🔥" wins over "❤"; optional U+FE0F after every char
_keys = sorted(PREMIUM, key=len, reverse=True)
_RX = re.compile("|".join("".join(re.escape(c) + "\ufe0f?" for c in k) for k in _keys))
_SKIP = (raw.types.MessageEntityCode, raw.types.MessageEntityPre, raw.types.MessageEntityCustomEmoji,
         raw.types.MessageEntityTextUrl, raw.types.MessageEntityUrl)


def _u16(s: str) -> int:
    return len(s.encode("utf-16-le")) // 2


def emoji_entities(text: str, existing=None) -> list:
    """Custom-emoji entities for every known emoji in text (UTF-16 offsets, like Telegram)."""
    blocked = [(e.offset, e.offset + e.length) for e in (existing or []) if isinstance(e, _SKIP)]
    out, pos16, last = [], 0, 0
    for m in _RX.finditer(text):
        pos16 += _u16(text[last:m.start()])
        last = m.start()
        ln = _u16(m.group())
        if not any(a < pos16 + ln and pos16 < b for a, b in blocked):
            eid = PREMIUM.get(m.group().replace("\ufe0f", ""))
            if eid:
                out.append(raw.types.MessageEntityCustomEmoji(offset=pos16, length=ln, document_id=int(eid)))
                if len(out) >= MAX_PER_MSG:
                    break
    return out


def _is_bot(client) -> bool:
    return bool(client and getattr(client, "bot_token", None))


_orig_parse = Parser.parse
_orig_invoke = Client.invoke


async def _parse(self, text, mode=None):
    res = await _orig_parse(self, text, mode)
    if STATE["on"] and _is_bot(self.client) and res.get("message"):
        try:
            ents = list(res.get("entities") or [])
            extra = emoji_entities(res["message"], ents)
            if extra:
                res["entities"] = sorted(ents + extra, key=lambda e: e.offset)
        except Exception as e:  # never break a message because of emojis
            log.warning("premium text skipped: %s", e)
    return res


def _strip(query) -> bool:
    """Remove our custom-emoji entities from a request. True if something was removed."""
    changed = False
    for obj in [query, getattr(query, "media", None)]:
        ents = getattr(obj, "entities", None)
        if ents:
            keep = [e for e in ents if not isinstance(e, raw.types.MessageEntityCustomEmoji)]
            if len(keep) != len(ents):
                obj.entities, changed = keep or None, True
    return changed


async def _invoke(self, query, *a, **kw):
    try:
        return await _orig_invoke(self, query, *a, **kw)
    except BadRequest as e:
        if _is_bot(self) and STATE["on"] and _strip(query):
            log.warning("Telegram refused premium emojis in text (%s) – plain emojis from now on", e)
            STATE["on"] = False
            return await _orig_invoke(self, query, *a, **kw)
        raise


def install():
    if Parser.parse is not _parse:
        Parser.parse = _parse
        Client.invoke = _invoke
