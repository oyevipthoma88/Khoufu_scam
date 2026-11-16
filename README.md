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
- **🧪 Icon Test** – shows whether premium button icons are LIVE or BLOCKED, the exact Telegram reason, and which owners have Premium.
  Telegram only shows button icons if the account that **created the bot in @BotFather** has Telegram Premium (never in channels).
  While blocked, buttons keep their normal emojis and the bot re-tests every `ICON_PROBE_EVERY` seconds (default 1800) – icons come back by themselves.
- Buttons are coloured: green = apply/add, red = reset/remove, blue = navigation.


## New settings
- `LOG_CHANNEL` – channel ID for detailed logs (bot must be admin)
- `PARALLEL_ACCOUNTS` – accounts worked on at the same time (default 3)
- `MAX_FLOOD_WAIT` – longest flood wait the bot will sit through (default 900s)

## 🛍 Shop (for users)

Users press /start → must join your channels → ✅ Verify → menu:
📱 Buy Account • 📢 Buy Channel • 👥 Buy Group • 🤖 Buy Bot • 📚 Buy Method • 👑 Owner • 📣 Update Channel • 📖 Tutorial • 🎟 Redeem Code • 🧾 My Orders

- **Account:** stock is shown per country → pay (QR/UPI) → send screenshot/UTR → owner approves → buyer gets number + 2FA, taps **Get OTP** (code sent with spaces) → taps **I'm logged in** → bot checks the buyer's session exists and logs its own session out.
- **Channel / Group:** pay → approved → join link → **Yes, I joined** → bot verifies membership, makes the buyer admin and transfers ownership (or to another numeric ID). If Telegram blocks it the owner gets a "manual transfer" alert.
- **Bot / Method:** delivered right after approval.
- Unpaid orders are cancelled after `ORDER_TIMEOUT` minutes and the item goes back to stock. One item can never be sold twice.

## 🛍 Shop Admin (owner)

Owner menu → **🛍 Shop Admin**: Add Stock (Account / Channel / GC / Bot / Method), Stock List, Orders (approve / reject / deliver again), Set Price (per kind, per country for accounts), Set QR, Set UPI, Redeem Codes, Must-Join, Update Link, Contact, Tutorial, Seller Accounts.

Notes:
- The bot must be **admin** in every must-join channel.
- Ownership transfer needs the seller account's **2-step password**, and Telegram only allows it ~24h after that session was created.

## Tests
`pip install pytest pytest-asyncio mongomock-motor && pytest -q`

## Prices per account type
Normal, Scam Tag and Fake Tag accounts have **separate prices**, even for the same country.
Shop Admin → Set Price → Account → pick the type → pick a country (or Default).
A Scam/Fake account never uses the Normal price – set its price, or it stays hidden from buyers.

## Log channel
Logs arrive as neat cards with a `#tag` (e.g. `#error`, `#order`, `#payment`) so you can search them in the channel.
`LOG_TZ_OFFSET` – log time zone in minutes (default `330` = India).

## Premium emojis in text
Every emoji in the bot's messages (menus, orders, alerts, logs) is shown as a matching premium emoji automatically.
If Telegram refuses them, the bot falls back to normal emojis by itself. `PREMIUM_TEXT=0` turns it off.
