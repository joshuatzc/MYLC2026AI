# 🏛️ Build the Biggest Church — MYLC 2026 Telegram Bot

A Telegram-based church-building game for MYLC 2026. Groups compete to grow their church population through strategic decisions made via Telegram commands. A FastAPI backend serves the admin API and keeps the bot alive.

---

## 📋 Prerequisites

| Requirement | Minimum Version |
|---|---|
| Python | 3.11+ |
| pip | Latest |
| Docker + Docker Compose | v24+ (Docker route only) |

You will also need a **Telegram Bot Token** from [@BotFather](https://t.me/BotFather).

---

## ⚙️ Environment Setup

Regardless of which deployment method you choose, you must configure your environment variables first.

**1. Copy the example file:**
```bash
cp .env.example .env
```

**2. Edit `.env` and fill in your values:**
```env
# Your bot token from @BotFather
TELEGRAM_BOT_TOKEN=your_bot_token_here

# Password leaders use to authenticate themselves
LEADER_PASSWORD=change-me

# Database URL (SQLite is the default — no extra setup needed)
DATABASE_URL=sqlite+aiosqlite:///./church_game.db

# FastAPI server settings
API_HOST=0.0.0.0
API_PORT=8000
SECRET_KEY=change-me-in-production

# Starting population for each church group
STARTING_POPULATION=10
```

> **⚠️ Never commit your `.env` file to Git.** It is already listed in `.gitignore`.

---

## 🚀 Option A — Running with Uvicorn (Local / Dev)

This method runs the bot directly on your machine using a Python virtual environment.

### 1. Create and activate a virtual environment

```bash
python3 -m venv .venv
source .venv/bin/activate      # macOS / Linux
# .venv\Scripts\activate       # Windows
```

### 2. Install dependencies

```bash
pip install -r requirements.txt
```

### 3. Start the server

```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

For development with **auto-reload** on file changes:

```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

The API will be available at `http://localhost:8000`.  
Interactive API docs: `http://localhost:8000/docs`

The Telegram bot and database seeding both start automatically alongside the FastAPI server.

---

## 🐳 Option B — Running with Docker

This method containerises the entire application for a consistent, portable deployment. Recommended for production or running on a NAS (e.g. Synology).

### 1. Build and start the container

```bash
docker compose up -d --build
```

This will:
- Build the image from the `Dockerfile`
- Load your secrets from `.env`
- Create a named Docker volume (`mylc_data`) to persist the SQLite database across restarts
- Start the container in the background
- **Automatically seed** stations, levels, prerequisites, and groups on first boot

### 2. Check that it's running

```bash
docker compose ps
docker compose logs -f
```

The container has a health check built in — it will show as `healthy` once the API is responding.

### 3. Stop the bot

```bash
docker compose down
```

> **Note:** Your database is stored in the `mylc_data` Docker volume and is **not** deleted when you run `docker compose down`. To also remove the volume, add `--volumes`.

### Updating to a new version

```bash
git pull
docker compose up -d --build
```

Docker will rebuild only the layers that changed, then restart the container. On startup, any new station data (new hints, multipliers, etc.) defined in `scripts/seed.py` is automatically applied — **game progress is never touched**.

---

## 📁 Project Structure

```
.
├── app/
│   ├── main.py          # FastAPI entrypoint; launches bot polling on startup
│   ├── bot_runner.py    # Aiogram bot + dispatcher factory
│   ├── config.py        # Environment variable parsing (pydantic-settings)
│   ├── database.py      # SQLAlchemy async engine + session factory
│   ├── models.py        # ORM models (Group, Player, GameState, …)
│   ├── bot/             # Aiogram handlers and FSM logic
│   ├── routers/
│   │   └── admin.py     # FastAPI admin REST endpoints
│   └── services/        # Business logic layer
├── scripts/
│   ├── seed.py          # Station/level/group definitions — auto-applied on startup
│   └── reseed.sh        # Dev helper: wipe DB and reseed via the admin API
├── Dockerfile
├── docker-compose.yml
├── requirements.txt
└── .env.example
```

---

## 🛠️ Admin API

Once running, the admin REST API is available at `http://localhost:8000`.

| Endpoint | Method | Auth | Description |
|---|---|---|---|
| `GET /` | — | — | Health check |
| `GET /docs` | — | — | Interactive Swagger UI |
| `GET /admin/leaderboard` | — | — | Live leaderboard |
| `GET /admin/stations` | — | — | List all stations and levels |
| `GET /admin/groups` | — | — | List all groups |
| `POST /admin/reseed` | `X-Admin-Key` | Wipe DB and reseed from scratch (dev reset) |

---

## 🔄 Resetting Game State (Dev)

To wipe all game progress and start fresh without exec-ing into the container:

```bash
bash scripts/reseed.sh
# or against a remote host:
bash scripts/reseed.sh 192.168.1.100 8000
```

The script reads `SECRET_KEY` from your `.env` automatically and calls `POST /admin/reseed`.

> **⚠️ This is destructive.** All group progress, populations, and chat states are permanently deleted. Station definitions are reloaded from `seed.py`.

---

## 🐛 Troubleshooting

**Bot doesn't respond after startup**  
→ Double-check that `TELEGRAM_BOT_TOKEN` in your `.env` is correct and the bot has been started in Telegram.

**Port 8000 is already in use**  
→ Change `API_PORT` in `.env` and update the port mapping in `docker-compose.yml` accordingly.

**Database errors on Docker**  
→ The container uses `/data/church_game.db` (mapped to the `mylc_data` volume). Ensure `DATABASE_URL` in the Docker environment override in `docker-compose.yml` is not overwritten in your `.env`.

**Permission errors on the database file (non-Docker)**  
→ Ensure the directory is writable by your current user.
