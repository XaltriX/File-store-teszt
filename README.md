# File Store Bot

Telegram file-store bot: permanent deep links, force-subscription verification (public membership check or
private join-request events), per-file content protection, multilingual UI (EN/HI/BN/ES/AR/PT/ZH), full
admin panel in inline buttons, broadcasts, MongoDB persistence, Heroku worker deployment.

Stack: Python 3.12, aiogram 3.13 (Bot API, no `API_ID`/`API_HASH`), PyMongo async API, pytest.

## Design summary

| Area | Decision |
|---|---|
| Delivery | Stores Telegram `file_id` only (nothing downloaded). Sends by stored type with `protect_content` and a **Share** URL button (`t.me/share/url?url=<encoded deep link>`). |
| Links | 128-bit `secrets.token_urlsafe` tokens (22 chars), unique index. Many links per file; revoking one never touches others. |
| Flows | Persisted in `flows`. Channels are chosen once (least-presented, random tie-break, distinct) and never re-rolled on Verify. `presented` is incremented atomically at assignment. |
| Public channel | Step completes only when `getChatMember` says member/admin/creator. API errors never complete a step. |
| Private channel | Step completes only after a `chat_join_request` event (deduplicated by chat+user+request time). Requests are **never approved/declined** and are counted separately from joins. |
| Idempotency | `mark_done` is an atomic conditional update; `claim_completion` (`find_one_and_update`) elects exactly one deliverer; 2s `/start` guard and 5s repeat-access guard use an atomic TTL guard. |
| Delivery failure | Flow is re-opened so the user can press Verify again. |
| Repeat access | Completed flows are kept 30 days; opening the same link again delivers without re-verifying. After that, verification restarts. |
| Auto-delete | One persisted queue (`pending_deletes`) + one worker. Verification prompts/status messages only (default 60 s, admin-configurable, 0 = off). Delivered files are never auto-deleted. Survives restarts. |
| Broadcasts | `copy_message` of the admin's message. Job doc holds counters + cursor; per-recipient outcome is one small doc in `broadcast_deliveries` (unique `(bid, uid)`), so resume never re-sends. 20 msgs/s batches, bounded retries with backoff+jitter, blocked users marked and skipped, graceful shutdown marks the job `interrupted` and it resumes on next start. |
| Admin auth | `AdminAuthMiddleware` on the admin router checks `ADMIN_IDS` on every message and callback. Forged `a:*` callbacks from non-admins are rejected. |
| FSM | Admin input flows use in-memory FSM storage (short-lived by design). Everything durable is in MongoDB. |
| Updates | Polled sequentially (`handle_as_tasks=False`) so batch uploads keep order. Fine for <1,000 files/day. |

### Deliberate deviations / interpretations
- If fewer active channels exist than the configured steps (1–3), the flow requires fewer steps instead of repeating a channel.
- With zero active channels, files are delivered directly.
- Deep-link users with no saved language skip the language prompt (language is guessed from Telegram's `language_code` and saved), so a file is never blocked behind a menu. `/start` without a payload asks for the language once.
- Welcome/About custom text is plain text (escaped); first line becomes the bold title; `{first_name}` is supported. One custom text applies to all languages (the built-in default is translated).
- Admin operational labels (button captions, admin panels) are English; all user-facing text is translated. Admin text strings still live in `en.json` (`a_*` keys).
- Bot API cannot read channel history: **Import** works by forwarding channel messages to the bot, or by enabling **Watch** on a pool channel (bot must be channel admin) so new media posts are imported.
- Settings has two extra entries beyond the brief: Official Channels list and About text.
- `handlers/admin/welcome_editor.py` from the suggested tree is folded into `settings.py` (same router, preview-before-apply flow).

## Project layout
```
bot.py  config.py  Procfile  requirements.txt  .python-version  .env.example  pytest.ini
app/application.py (Ctx wiring)  app/dispatcher.py
app/database/{mongo,indexes}.py  app/database/repositories/*.py
app/services/{file,link,verification,channel_selector,delivery,broadcast,statistics,welcome}_service.py
app/handlers/{start,verification,language,callbacks,files}.py  app/handlers/admin/*.py
app/keyboards/*  app/middlewares/*  app/localization/*.json  app/states/*  app/utils/*
tests/  (fakes.py = in-memory Mongo + fake bot, so no database is needed to test)
```

## Local setup (Windows PowerShell)
```powershell
git clone <your-repo> file-store-bot; cd file-store-bot
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
notepad .env          # set BOT_TOKEN, MONGO_URL, ADMIN_IDS
python bot.py
pytest                # tests need no MongoDB or network
```
`ADMIN_IDS` = comma-separated numeric Telegram IDs (`OWNER_ID` also accepted). Get yours from @userinfobot.

## MongoDB
- **Atlas (recommended for Heroku):** create a free cluster → Database Access: add a user → Network Access: allow `0.0.0.0/0` (Heroku dynos have no fixed IP) → Connect → Drivers → copy the `mongodb+srv://…` string into `MONGO_URL`. Database name defaults to `filestore` (`MONGO_DB`).
- **Local:** install MongoDB Community, run `mongod`, use `MONGO_URL=mongodb://localhost:27017`.
Indexes (including TTL indexes) are created automatically at startup. No transactions are required.

## Telegram setup
1. Create the bot with @BotFather; put the token in `BOT_TOKEN`.
2. For each force-subscribe channel add the bot as **admin**. Public channels need no extra right. Private channels need **Invite users** so the bot can create a join-request link (otherwise paste an invite link created with *Request admin approval* enabled).
3. Send `/admin` to the bot and use **Channels → Add Channel**.
4. Set More Videos / Developer URLs in **Settings** (the buttons show "not configured" until you do; no URLs are invented).

## Heroku deployment
```powershell
heroku login
heroku create your-filestore-bot
heroku config:set BOT_TOKEN="123456:ABC..." MONGO_URL="mongodb+srv://user:pass@cluster/db" ADMIN_IDS="123456789" -a your-filestore-bot
# optional: MONGO_DB, LOG_LEVEL=INFO
git push heroku main
heroku ps:scale web=0 worker=1 -a your-filestore-bot
heroku logs --tail -a your-filestore-bot
```
Config Vars: `BOT_TOKEN`, `MONGO_URL`, `ADMIN_IDS` (required); `MONGO_DB`, `LOG_LEVEL`, `DEFAULT_LANGUAGE` (optional).
Never run the bot locally and on Heroku with the same token at once (Telegram allows one polling consumer; you will get conflict errors). On startup the bot calls `deleteWebhook` and exits with a clear error if MongoDB or the token is invalid.

**Restart / recovery:** `heroku ps:restart worker`. On SIGTERM the bot stops polling, interrupts any running broadcast (status `interrupted`) and closes connections. On next start: interrupted broadcasts resume without duplicates, active verification flows continue, due auto-deletes are processed. A temporary deletion is delayed (not lost) while the dyno is down.

## Known limitations
- Content protection stops forwarding/saving in supported clients only; it cannot stop screenshots or other copying.
- Verification prompts are deleted after the auto-delete timer (default 60 s). The flow persists: reopening the link re-sends the same channel's card. Set the timer to Off if you prefer permanent prompts.
- Admin FSM input state is in memory; a restart cancels a half-finished admin input (nothing durable is lost).
- Throttling is per-process memory (single worker dyno assumed).
- Sequential update handling means a very slow Telegram call delays other updates; fine at the stated scale.
