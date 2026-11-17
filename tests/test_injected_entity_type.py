import pytest
from pyrogram import enums, raw, types
import premium_text

def test_entity_missing_handler():
    assert issubclass(premium_text.InjectedEmoji, raw.types.MessageEntityCustomEmoji)
    # enums.MessageEntityType(InjectedEmoji) resolves to CUSTOM_EMOJI via _missing_
    member = enums.MessageEntityType(premium_text.InjectedEmoji)
    assert member is enums.MessageEntityType.CUSTOM_EMOJI

def test_unknown_class_still_raises():
    class UnknownSubclass:
        pass
    with pytest.raises(ValueError):
        enums.MessageEntityType(UnknownSubclass)

@pytest.mark.asyncio
async def test_entity_parse_custom_emoji():
    ent = premium_text.InjectedEmoji(offset=0, length=2, document_id=12345)
    parsed = await types.MessageEntity._parse(None, ent, {})
    assert parsed.type is enums.MessageEntityType.CUSTOM_EMOJI
    assert parsed.custom_emoji_id == "12345"

def test_strip_mutates_list_in_place():
    ents = [premium_text.InjectedEmoji(offset=0, length=2, document_id=12345)]
    query = raw.functions.messages.SendMessage(
        peer=raw.types.InputPeerSelf(),
        message="hi",
        random_id=1,
        entities=ents
    )
    changed = premium_text._strip(query)
    assert changed is True
    assert len(ents) == 0
