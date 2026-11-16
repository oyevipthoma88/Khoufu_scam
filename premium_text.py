"""Premium emojis inside message TEXT (not only buttons).

install() patches pyrogram once, so EVERY message / caption / edit the BOT sends
(menus, orders, logs, alerts …) gets its plain emojis turned into matching premium
emojis automatically – no change needed at the 100+ places that send text.

Safe:
- only the bot client is touched (seller / user accounts are never changed)
- emojis inside `code` / ```pre``` blocks are left alone
- max 100 premium emojis per message (Telegram limit)
- recognized emoji errors retry only that request without injected entities
- original custom emojis, supplied entities and captions are preserved
"""
import logging
import os
import regex
from pyrogram import utils, types
from emoji_registry import emoji_id

from pyrogram import Client, raw
from pyrogram.errors import BadRequest
from pyrogram.parser.parser import Parser

from premium_emoji import PREMIUM

log = logging.getLogger("premium_text")
STATE = {"on": os.environ.get("PREMIUM_TEXT", "1") != "0"}
MAX_PER_MSG = 100

class InjectedEmoji(raw.types.MessageEntityCustomEmoji):
    """Distinct type retains the Telegram constructor while marking only our entities."""
    pass


_SKIP = (raw.types.MessageEntityCode, raw.types.MessageEntityPre, raw.types.MessageEntityCustomEmoji,
         raw.types.MessageEntityTextUrl, raw.types.MessageEntityUrl)


def _u16(s: str) -> int:
    return len(s.encode("utf-16-le")) // 2


def emoji_entities(text: str, existing=None) -> list:
    """Custom-emoji entities for every known emoji in text (UTF-16 offsets, like Telegram)."""
    blocked = [(e.offset, e.offset + e.length) for e in (existing or []) if isinstance(e, _SKIP)]
    budget = MAX_PER_MSG - sum(isinstance(e, raw.types.MessageEntityCustomEmoji) for e in (existing or []))
    if budget <= 0:
        return []
    out, pos16, last = [], 0, 0
    for m in regex.finditer(r"\X", text):
        pos16 += _u16(text[last:m.start()])
        last = m.start()
        ln = _u16(m.group())
        if not any(a < pos16 + ln and pos16 < b for a, b in blocked):
            eid = emoji_id(m.group())
            if eid:
                out.append(InjectedEmoji(offset=pos16, length=ln, document_id=int(eid)))
                if len(out) >= budget:
                    break
    return out


def _is_bot(client) -> bool:
    return bool(client and getattr(client, "bot_token", None))


_orig_parse = Parser.parse
_orig_invoke = Client.invoke
_orig_entities = utils.parse_text_entities
_orig_entity_parse = types.MessageEntity._parse


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


async def _entities(client, text, parse_mode, entities):
    result = await _orig_entities(client, text, parse_mode, entities)
    if STATE["on"] and _is_bot(client) and result.get("message"):
        existing = list(result.get("entities") or [])
        result["entities"] = sorted(existing + emoji_entities(result["message"], existing), key=lambda e: e.offset) or None
    return result


async def _entity_parse(client, entity, users):
    # send_message()/edit via UpdateShortSentMessage parses the entities we SENT back into
    # pyrogram types; enums.MessageEntityType(InjectedEmoji) raises "is not a valid
    # MessageEntityType". Present our subclass as the plain custom-emoji entity it is on the wire.
    if isinstance(entity, InjectedEmoji):
        entity = raw.types.MessageEntityCustomEmoji(
            offset=entity.offset, length=entity.length, document_id=entity.document_id)
    return await _orig_entity_parse(client, entity, users)


def _strip(query) -> bool:
    changed = False
    seen = set()
    def walk(obj):
        nonlocal changed
        if obj is None or id(obj) in seen: return
        seen.add(id(obj))
        if isinstance(obj, (list, tuple)):
            for item in obj: walk(item)
            return
        ents = getattr(obj, "entities", None)
        if ents:
            keep = [e for e in ents if not isinstance(e, InjectedEmoji)]
            if len(keep) != len(ents): obj.entities, changed = keep or None, True
        for attr in ("media", "multi_media"): walk(getattr(obj, attr, None))
    walk(query)
    return changed


async def _invoke(self, query, *a, **kw):
    try:
        return await _orig_invoke(self, query, *a, **kw)
    except BadRequest as e:
        emoji_error = any(code in str(e).upper() for code in ("CUSTOM_EMOJI", "DOCUMENT_INVALID", "PREMIUM_ACCOUNT_REQUIRED"))
        if _is_bot(self) and STATE["on"] and emoji_error and _strip(query):
            log.warning("Telegram refused premium text (%s); retrying only this request", e)
            return await _orig_invoke(self, query, *a, **kw)
        raise


def install():
    if Parser.parse is not _parse:
        Parser.parse = _parse
        utils.parse_text_entities = _entities
        types.MessageEntity._parse = staticmethod(_entity_parse)
        Client.invoke = _invoke
