"""Regression: '<class premium_text.InjectedEmoji> is not a valid MessageEntityType'."""
import os
import sys

os.environ.setdefault("API_ID", "1"); os.environ.setdefault("API_HASH", "x")
os.environ.setdefault("BOT_TOKEN", "1:x"); os.environ.setdefault("MONGO_URI", "mongodb://localhost")
os.environ.setdefault("OWNER_IDS", "1")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pyrogram import enums, types

import premium_text


async def test_injected_emoji_is_parsed_as_custom_emoji():
    premium_text.install()
    ent = premium_text.InjectedEmoji(offset=3, length=2, document_id=5368324170671202286)
    parsed = await types.MessageEntity._parse(None, ent, {})
    assert parsed.type is enums.MessageEntityType.CUSTOM_EMOJI
    assert (parsed.offset, parsed.length) == (3, 2)
    assert parsed.custom_emoji_id == "5368324170671202286"


async def test_plain_entities_still_parse():
    from pyrogram import raw
    premium_text.install()
    parsed = await types.MessageEntity._parse(None, raw.types.MessageEntityBold(offset=0, length=4), {})
    assert parsed.type is enums.MessageEntityType.BOLD


def test_install_is_idempotent():
    premium_text.install()
    first = types.MessageEntity._parse
    premium_text.install()
    assert types.MessageEntity._parse is first
