# Bot architecture
- All bot text and keyboard icons resolve through emoji_registry.py and the Telegram-verified manifest; this prevents unverified IDs reaching Telegram.
- Premium text augmentation runs after parsing and explicit-entity conversion; this preserves formatting while covering captions, edits and copies.
- Injected custom emojis use a distinct raw entity subclass with the original wire constructor; request-local fallback can preserve original user entities.
- No module may `import main`; runtime-shared state (e.g. EXTRA_OWNERS) lives in config.py, and main.py aliases itself in sys.modules; under `python main.py` a fresh `import main` re-runs the file and binds helpers to a second, never-started Client.
- shop_core.bind/ui.bind keep the first client unless force=True, and retry() reconnects once on ConnectionError; a stray client must never replace the running bot.
- Keyboards are built via ui.build inside ui.safe_send; the icon engine judges Telegram's echo (private/group only), falls back to plain-emoji labels and re-probes, because icons need the BotFather owner's Premium.
- Owner sale decisions go through db.claim_sale (atomic) so two owners can never both act on one sale.
- premium_text patches `types.MessageEntity._parse` so InjectedEmoji maps to a plain custom-emoji entity; send_message re-parses the entities it just sent, and the enum lookup rejects unknown subclasses.
