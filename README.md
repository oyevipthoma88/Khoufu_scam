# Khoufu Account Manager Bot

Owner-only Telegram bot (multiple owners supported).

[![Deploy](https://www.herokucdn.com/deploy/button.svg)](https://www.heroku.com/deploy?template=https://github.com/oyevipthoma88/Khoufu_scam)

## Features
- ➕ Add accounts by **string session** (Pyrogram / Telethon) or **phone login** (OTP + 2FA)
- ⚙️ **Custom** panel → Set User / Set Channel / Set Group → Set Name, Bio, PFP, Username
- On each account: profile changed + **10 public channels + 10 public groups** created and configured
- Usernames: tries `name`, `name1`, `name2`… and sets the first free one (counter shared across all accounts)
- 🚀 **Apply All** button, 🤖 **Auto Apply** for new logins
- 👤 **My Accounts**: all accounts, ♻️ Reset (restores old name/bio/photo/username, randomizes channels/groups), 🔗 all links at once
- 📖 Tutorial, 📊 Status
- MongoDB storage: nothing is lost on restart

## Heroku deploy
1. Create a MongoDB (MongoDB Atlas free) and copy the connection string. Allow access from `0.0.0.0/0`.
2. Get `API_ID` / `API_HASH` from https://my.telegram.org and `BOT_TOKEN` from @BotFather.
3. Heroku → New app → Deploy → connect this GitHub repo → Deploy branch `main`.
4. Settings → Config Vars: `API_ID`, `API_HASH`, `BOT_TOKEN`, `MONGO_URI`, `OWNER_IDS` (e.g. `12345 67890`).
5. Resources → turn **worker** ON (`heroku ps:scale worker=1`).

## Config vars
| Var | Default | Meaning |
|---|---|---|
| CHANNEL_COUNT | 10 | channels per account |
| GROUP_COUNT | 10 | groups per account |
| ACTION_DELAY | 3 | seconds between Telegram actions |
| DB_NAME | khoufu_bot | Mongo database name |

## Notes
- Send OTP with spaces (`1 2 3 4 5`), Telegram blocks plain codes sent in chats.
- Telegram limits how many public links one account can own; extra chats stay private with an invite link.
- Use `{n}` inside a channel/group name or bio to number them.

## Owner Panel
- **🖼 Start Pic** – photo shown on /start, saved in MongoDB (survives restarts).
- **✨ Button Emojis** – tap a button name, send any premium emoji: it becomes the button icon (saved in MongoDB). If Telegram rejects icons, the bot falls back to plain buttons automatically.
- Buttons are coloured: green = apply/add, red = reset/remove, blue = navigation.
