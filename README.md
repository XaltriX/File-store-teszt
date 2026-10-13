# LUST TEACHER BOT

Telegram file-store bot: permanent deep links, DB-channel auto-save, force-subscription with **auto-verify**
(public membership / private join requests), per-file content protection, UI in English, Hindi, Bengali,
Marathi, Telugu, Tamil, Gujarati, bold inline buttons, welcome photo/video/GIF, inline admin panel,
broadcasts, MongoDB persistence, Heroku worker deployment.

Stack: Python 3.12, aiogram 3.13 (Bot API, no `API_ID`/`API_HASH`), PyMongo async API, pytest.

## Design summary

| Area | Decision |
|---|---|
| Delivery | Stores Telegram `file_id` only (nothing downloaded). Sends by stored type with `protect_content` and a **Share** URL button (`t.me/share/url?url=<encoded deep link>`). |
| Links | 128-bit `secrets.token_urlsafe` tokens (22 chars), unique index. Many links per file; revoking one never touches others. |
| Flows | Persisted in `flows`. Channels are chosen once (least-presented, random tie-break, distinct) and never re-rolled on Verify. `presented` is incremented atomically at assignment. |
| Language | Asked on first contact for everyone who has not explicitly chosen one (`lang_chosen`), also when they arrive through a file link; accounts whose language was only auto-detected by an older version are asked once more. |
| Join-request stats | `Join requests` = **unique users** who requested through the bot's current link, cumulative since stats began (not Telegram's *pending* count). Repeat requests by the same user and requests through other/old invite links are counted separately per channel. |
| Auto-verify | On link open the bot first checks every assigned channel: users who already joined (or already sent a join request) skip the card. While waiting, `chat_member` updates (public/any channel) and `chat_join_request` updates advance the flow and deliver the file automatically, with no button press. Requires the bot to be **admin** in each channel. |
| DB channel | Files → DB Channel: set a channel (bot = admin). Every file posted there is saved and its link is DM'd to the admin who set it, plus optionally replied inside the channel (needs "Post messages"). Admins can also just send a file to the bot (no button) to get a link. |
| Welcome media | Photo, video or GIF (50 MB bot limit; 3-8 s loops work best). Saved immediately, preview shown after (no pending "Apply" step). |
| Bold buttons | Telegram buttons can't carry HTML, so Latin letters/digits use Unicode bold glyphs. Indic scripts have no bold glyphs and stay normal. |
| Public channel | Step completes only when `getChatMember` says member/admin/creator. API errors never complete a step. |
| Private channel | Step completes only after a `chat_join_request` event (deduplicated by chat+user+request time). Requests are **never approved/declined** and are counted separately from joins. |
| Idempotency | `mark_done` is an atomic conditional update; `claim_completion` (`find_one_and_update`) elects exactly one deliverer; 2s `/start` guard and 5s repeat-access guard use an atomic TTL guard. |
| Join requests | A stored join request counts until it turns into membership (then it is consumed, so leaving later requires a new request). A request still pending keeps counting for up to 7 days, because Telegram does not let the user send a second one. If the membership lookup itself fails, a stored request is never trusted. Consumption relies on seeing the user as a member once (link open or `chat_member` update). |
| Delivery failure | Flow is re-opened so the user can press Verify again. |
| Re-verification | **Every file request is verified again.** Users still in their channels get the file instantly (no card); a user who left a channel is asked to rejoin the same channel (their previous channels are kept so rotation doesn't hand them a new one each time). Completed flows are kept 14 days only to remember those channels. |
| Auto-delete | One persisted queue (`pending_deletes`) + one worker. Status/error messages use the admin timer (default 60 s, 0 = off). Verification cards live for the whole 30-min flow and are removed on delivery. Delivered files are never auto-deleted. Survives restarts. |
| Broadcasts | `copy_message` of the admin's message. Job doc holds counters + cursor; per-recipient outcome is one small doc in `broadcast_deliveries` (unique `(bid, uid)`), so resume never re-sends. 20 msgs/s batches, bounded retries with backoff+jitter, blocked users marked and skipped, graceful shutdown marks the job `interrupted` and it resumes on next start. |
| Admin panel | Admins see a **🛠 Admin Panel** button under the `/start` welcome card (`/admin` still works). Authorization is checked server-side on every admin callback. |
| Admin auth | `AdminAuthMiddleware` on the admin router checks `ADMIN_IDS` on every message and callback. Forged `a:*` callbacks from non-admins are rejected. |
| FSM | Admin input flows use in-memory FSM storage (short-lived by design). Everything durable is in MongoDB. |
| Concurrency | Updates are handled concurrently: different users in parallel, the same user in order (per-user lock), global cap of 40 handlers, and a 45 s hard limit per update so one stuck call can never freeze the bot. |
| Latency | Settings (5 s) and user records (60 s) are cached in-process and invalidated on write, so button presses do 0 DB reads; link opens run their independent lookups in parallel and all channel membership checks concurrently. Every MongoDB operation has a socket/connect/pool timeout and the Telegram HTTP session has a 25 s timeout. |
| Delivered files | Sent by `copy_message` from the DB-channel copy (durable), falling back to the stored `file_id`. Files sent straight to the bot are copied into the DB channel automatically. |
| File auto-delete | Settings → File Auto-delete. **Auto (default):** each delivered media is removed after its own length + 1 minute (a 2-min video stays 3 min; max 24 h); files without a length (documents, photos) use 5 min; or pick a fixed 1m-1h / Off. A short notice is removed together with the file; the user can reopen the link to get it again. Persisted, survives restarts, deletes run concurrently. |

### Deliberate deviations / interpretations
- If fewer active channels exist than the configured steps (1–3), the flow requires fewer steps instead of repeating a channel.
- With zero active channels, files are delivered directly.
- A join-request event on any pool channel (even one added in public mode) completes that step, since such an event only exists when the channel requires approval.
- Welcome card carries an "18+ only" line (edit `welcome` in `app/localization/*.json` to remove).
- First contact always asks for the language, including when the user arrives through a file link; the pending link is carried inside the language buttons and the file flow continues right after the choice.
- Welcome/About custom text is plain text (escaped); first line becomes the bold title; `{first_name}` is supported. One custom text applies to all languages (the built-in default is translated).
- Admin operational labels (button captions, admin panels) are English; all user-facing text is translated. Admin text strings still live in `en.json` (`a_*` keys).
- Manual Add File / Batch / Generate Links / Import were removed: use the DB channel, or just send a file to the bot.
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
4. Optional: Files → **DB Channel** (private channel, bot admin) so every posted file gets a link automatically.
5. Set More Videos / Developer URLs in **Settings** (the buttons show "not configured" until you do; no URLs are invented).

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

## How it stays fast under a crowd
Design rules (inspired by studying a fast reference bot, not copied from it):
- **Returning users cost 0 DB round-trips.** The channels a user last satisfied are remembered on their user record; on the next link open the bot only re-checks live membership of those channels (concurrently, behind a global cap) and delivers. No flow document, no writes.
- **New users** cost ~3-4 round-trips (user, active flow, create flow; a few more on completion).
- **Cached reads:** settings (5 s), user records (60 s), link+file resolution (30 s, invalidated automatically on revoke/archive/delete/protect changes), channel list (20 s, invalidated on admin changes). Join-request lookups use an in-memory set (warmed at startup), so a normal member check never touches the DB.
- **Write-behind:** counters, last-activity and auto-delete schedules are aggregated in memory and flushed every 1.5 s in a handful of writes (and on shutdown), so traffic volume doesn't multiply DB writes. Worst case on a hard crash: ~1.5 s of statistics/delete schedules.
- **Telegram protection:** `getChatMember` is capped (12 concurrent) with a 5 s positive cache; every outgoing call goes through a flood-control middleware that waits out `retry_after` and caps simultaneous requests (30).
- **Admins bypass the global handler cap**, so the panel stays responsive while users queue.
- `tests/test_load_profile.py` counts DB round-trips: 299 simultaneous returning users = 0 round-trips.

Optional: on platforms that need a bound port (Heroku *web* dyno, Render, Koyeb) set `PORT` and the bot also serves `GET /health`; for Heroku keep `worker`.

## Performance tips
- Create the Atlas cluster in the **same region as your Heroku app** (Heroku has US/EU only). Every DB round trip pays that latency.
- Use at least a Basic dyno for the worker if the free pool throttles; watch `heroku logs --tail` for lines like `Update id=... is handled. Duration N ms`.

## Known limitations
- If an update hits the 45 s hard limit it is cancelled mid-way; the user simply reopens the link.
- Content protection stops forwarding/saving in supported clients only; it cannot stop screenshots or other copying.
- Verification cards are deleted after 30 minutes (the flow lifetime); reopening the link restarts verification.
- Telegram only sends `chat_member` updates for channels where the bot is admin; without that, users must press Verify.
- Admin FSM input state is in memory; a restart cancels a half-finished admin input (nothing durable is lost).
- Throttling is per-process memory (single worker dyno assumed).
- Sequential update handling means a very slow Telegram call delays other updates; fine at the stated scale.
