import os
import sys
os.environ.setdefault('API_ID', '1')
os.environ.setdefault('API_HASH', 'x')
os.environ.setdefault('BOT_TOKEN', '1:x')
os.environ.setdefault('MONGO_URI', 'mongodb://localhost')
os.environ.setdefault('OWNER_IDS', '1')
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from types import SimpleNamespace
from pyrogram import enums, raw, utils
from pyrogram.types import InlineKeyboardButton, MessageEntity
from pyrogram.parser.parser import Parser
from emoji_registry import ENTRIES, VERIFIED_IDS, valid_id, verify_custom_id
from premium_emoji import PREMIUM
import premium_text
from ui import B, U, build


def test_all_configured_ids_have_telegram_verification():
    assert set(PREMIUM.values()) <= VERIFIED_IDS
    assert all(e['pack'] and valid_id(e['id']) for e in ENTRIES.values())


def test_reject_fake_numeric_shapes():
    for value in ['0', '-1', '١٢٣', str(2**63), 'not-an-id']:
        assert not valid_id(value)


async def test_unknown_id_requires_telegram_document():
    class C:
        async def get_custom_emoji_stickers(self, ids):
            assert ids == ['123']
            return []
    assert await verify_custom_id(C(), '123') is None


def test_all_button_types_get_verified_icons():
    markup = build([[B('✅Paid', 'paid'), U('🔗 Support', 'https://t.me/support'),
                     InlineKeyboardButton('Next', callback_data='next')]])
    assert all(b.icon_custom_emoji_id in VERIFIED_IDS for b in markup.inline_keyboard[0])
    assert markup.inline_keyboard[0][0].text == 'Paid'
    assert markup.inline_keyboard[0][1].url == 'https://t.me/support'
    assert markup.inline_keyboard[0][2].text == 'Next'


def test_every_emoji_cluster_has_one_whole_entity():
    for key, entry in ENTRIES.items():
        entities = premium_text.emoji_entities(key)
        assert len(entities) == 1, key
        assert entities[0].document_id == int(entry['id'])
        assert entities[0].length == premium_text._u16(key)


def test_no_partial_skin_tone_or_joined_entity():
    assert premium_text.emoji_entities('👍🏿') == []
    assert premium_text.emoji_entities('😀\u200d😀') == []


def test_existing_custom_emoji_counts_toward_budget():
    original = [raw.types.MessageEntityCustomEmoji(offset=0, length=2, document_id=1)
                for _ in range(100)]
    assert premium_text.emoji_entities('✅ ✅', original) == []


async def test_explicit_entities_still_get_premium():
    premium_text.install()
    class C:
        bot_token = '1:x'
        parse_mode = enums.ParseMode.DEFAULT
        def __init__(self): self.parser = Parser(self)
    result = await utils.parse_text_entities(C(), '✅ Paid', None,
                [MessageEntity(type=enums.MessageEntityType.BOLD, offset=0, length=6)])
    assert result['message'] == '✅ Paid'
    assert any(isinstance(e, raw.types.MessageEntityBold) for e in result['entities'])
    assert sum(isinstance(e, raw.types.MessageEntityCustomEmoji) for e in result['entities']) == 1


def test_fallback_preserves_original_custom_emoji_and_nested_caption():
    original = raw.types.MessageEntityCustomEmoji(offset=0, length=2, document_id=123)
    extra = premium_text.emoji_entities('✅')[0]
    caption = SimpleNamespace(entities=[original, extra])
    query = SimpleNamespace(multi_media=[caption])
    assert premium_text._strip(query)
    assert caption.entities == [original]


def test_no_bot_message_emoji_is_unmapped():
    import ast
    import regex
    from pathlib import Path
    from emoji_registry import emoji_id
    missing = set()
    for path in Path(__file__).parent.parent.glob('*.py'):
        if path.name.startswith('premium_') or path.name == 'emoji_registry.py':
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding='utf-8'))):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                for cluster in regex.findall(r'\X', node.value):
                    if regex.search(r'\p{Extended_Pictographic}|\p{Regional_Indicator}', cluster) and not emoji_id(cluster):
                        missing.add(cluster)
    assert not missing, missing


def test_injected_entities_serialize_like_telegram_custom_emoji():
    entity = premium_text.emoji_entities('✅')[0]
    original = raw.types.MessageEntityCustomEmoji(offset=entity.offset, length=entity.length, document_id=entity.document_id)
    assert entity.write() == original.write()
