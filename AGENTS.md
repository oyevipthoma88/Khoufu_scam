# Bot architecture
- All bot text and keyboard icons resolve through emoji_registry.py and the Telegram-verified manifest; this prevents unverified IDs reaching Telegram.
- Premium text augmentation runs after parsing and explicit-entity conversion; this preserves formatting while covering captions, edits and copies.
- Injected custom emojis use a distinct raw entity subclass with the original wire constructor; request-local fallback can preserve original user entities.
