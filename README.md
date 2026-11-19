# WardrobeAI

A private, Telegram-first wardrobe assistant for one authorized user. It uses FastAPI for
health/readiness, SQLite for durable state, Open-Meteo for Podgorica weather, and Mistral's
`zai-glm-5-3-flash` model for natural-language intent and stylist judgment.

The application deliberately does not use vector RAG or an agent framework. Application code
owns authorization, item IDs, confirmations, the four-day top-and-bottom cooldown, scheduler
idempotency, and every database write.

## Features

- Add, search, inspect, correct, soft-delete, and restore wardrobe items.
- Confirmation buttons for every wardrobe write and removal.
- Two weather-aware daily outfit options using owned items only, with explicit choice buttons.
- Hard four-calendar-day top-and-bottom pairing cooldown.
- Choosing an option never records it; “I wore it” confirms only the selected outfit.
- Confirmed outfit history and creative, wardrobe-aware shopping analysis.
- Short rolling conversation memory stored in SQLite; `/forget` clears it on demand.
- Monday–Friday 07:00 suggestions in `Europe/Podgorica`.
- Idempotent import of the original 23 wardrobe items and seven history entries.
- Restart-safe Telegram update and scheduled-send deduplication.
- Online SQLite backups, health/readiness endpoints, and redacted JSON logs.

## Local setup

Requirements: Python 3.12 and [uv](https://docs.astral.sh/uv/).

```powershell
uv sync --extra dev
Copy-Item .env.example .env
uv run pytest
uv run uvicorn app.main:app --host 127.0.0.1 --port 3000
```

Fill `.env` locally without committing it. The required secret values are:

- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_ALLOWED_USER_IDS`
- `TELEGRAM_DAILY_CHAT_ID`
- `MISTRAL_API_KEY`

To find the Telegram IDs without sharing the token, create the bot with BotFather, send the bot a
message, then call Telegram's `getUpdates` endpoint locally with the token held in an environment
variable. In a private chat, the `from.id` is the allowed user and `chat.id` is the daily chat.

Useful checks:

```powershell
uv run ruff check .
uv run pytest
uv run python -m app.cli migrate
uv run python -m app.cli import-legacy
uv run python -m app.cli backup
```

Do not point development at production data. The importer is idempotent, preserves the original
IDs, and never modifies files under `legacy_data/`.

## Runtime behavior

Telegram uses long polling; there is no public bot webhook. Unauthorized users and non-private
chats are rejected before the model is called. Logs omit Telegram message bodies, prompts, model
content, callback tokens, authorization headers, and secrets.

HTTP endpoints:

- `GET /health`: process liveness, available only from the VPS on `127.0.0.1:3000`.
- `GET /ready`: database integrity, scheduler state, and startup model-capability status, also
  localhost-only.

There is no public application hostname, webhook, Caddy route, or Cloudflare/DNS requirement.

If current weather cannot be verified, the assistant does not fabricate it. Scheduled delivery
retries at 07:15 and 07:45 and then sends one honest failure notice.

## GitHub production environment

Create a GitHub Environment named `production`.

Environment secrets:

- `VPS_SSH_PRIVATE_KEY`: contents of the restricted CI deployment key.
- `VPS_KNOWN_HOSTS`: the verified host-key line from the VPS operations folder.
- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_ALLOWED_USER_IDS`
- `TELEGRAM_DAILY_CHAT_ID`
- `MISTRAL_API_KEY`

Environment variables:

- `VPS_HOST=5.231.61.43`
- `VPS_USER=deploy`

The deployment workflow builds one immutable commit-SHA image, pushes it to private GHCR, uploads
Compose and a mode-600 `.env`, invokes the existing rootless deployment helper, and logs out of
GHCR. It does not run the unit tests or wait for a post-deploy health request. The workflow's
short-lived `GITHUB_TOKEN` is used for both push and the one deployment pull; no long-lived GHCR
token is required.

## One-time VPS bootstrap

Run these operations only after explicit approval. As the administrator, create the application
and backup export directories and assign them to the restricted deployment account:

```bash
sudo install -d -o deploy -g deploy -m 0750 /srv/apps/wardrobe-ai
sudo install -d -o deploy -g deploy -m 0750 /srv/backups/wardrobe-ai
```

Do not add a Caddy site or Cloudflare DNS record for this application. The container port is bound
only to `127.0.0.1:3000`, and GitHub Actions checks it through SSH.

## Operations

```bash
/home/deploy/bin/deploy-app wardrobe-ai status
/home/deploy/bin/deploy-app wardrobe-ai logs
/home/deploy/bin/deploy-app wardrobe-ai restart
curl -fsS http://127.0.0.1:3000/health
```

### Backup and off-VPS copy

The app creates a nightly online backup and retains 14 copies in its backup volume. Export them
to the runbook's host backup directory without copying the live database:

```bash
cd /srv/apps/wardrobe-ai
docker compose cp api:/backups/. /srv/backups/wardrobe-ai/
```

Copy exported backups weekly to a trusted machine using the administrator SSH account and encrypt
them at rest. A VPS-only backup is not sufficient.

### Restore

Stop the app, validate the chosen backup, then atomically replace the database through a one-off
container using the same named volumes:

```bash
cd /srv/apps/wardrobe-ai
docker compose down
docker compose run --rm --no-deps api python -m app.cli restore-check /backups/wardrobe-YYYY-MM-DD.sqlite3
docker compose run --rm --no-deps --entrypoint sh api -c 'cp /backups/wardrobe-YYYY-MM-DD.sqlite3 /data/wardrobe.sqlite3.restore && mv /data/wardrobe.sqlite3.restore /data/wardrobe.sqlite3'
/home/deploy/bin/deploy-app wardrobe-ai deploy
curl -fsS http://127.0.0.1:3000/ready
```

Preserve the current database with `python -m app.cli backup` before restoring.

### Rollback

Set `IMAGE_TAG` in `/srv/apps/wardrobe-ai/.env` to the previous known-good Git commit SHA and run:

```bash
/home/deploy/bin/deploy-app wardrobe-ai deploy
```

If the release applied an incompatible database migration, restore its matching pre-migration
backup before starting the older image.
