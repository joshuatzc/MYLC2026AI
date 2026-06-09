"""
main.py – FastAPI application entrypoint.

Starts the FastAPI app and, on startup, launches the Telegram bot
in the background using asyncio.
"""
from __future__ import annotations

import asyncio
import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.database import init_db
from app.routers import admin, public

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(
    title="Build the Biggest Church – Admin API",
    description="Backend API + admin interface for the church-building Telegram game.",
    version="1.0.0",
)

# Add CORS Middleware to support React frontend queries
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

import os
from fastapi.staticfiles import StaticFiles

# Register routers
app.include_router(admin.router)
app.include_router(public.router)

# Serve built static frontend files at root "/"
# Check app/static (for Docker multi-stage build copy) or frontend/dist (for local development)
static_path = os.path.join(os.path.dirname(__file__), "static")
local_dist_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "frontend", "dist")

if os.path.exists(static_path):
    app.mount("/", StaticFiles(directory=static_path, html=True), name="static")
    logger.info("Serving frontend static files from: %s", static_path)
elif os.path.exists(local_dist_path):
    app.mount("/", StaticFiles(directory=local_dist_path, html=True), name="static")
    logger.info("Serving frontend static files from: %s", local_dist_path)
else:
    logger.warning("No frontend static assets folder found. Root path '/' will not serve frontend.")



@app.on_event("startup")
async def on_startup() -> None:
    logger.info("Initialising database…")
    await init_db()

    logger.info("Seeding station data…")
    from scripts.seed import seed
    from app.database import AsyncSessionLocal
    async with AsyncSessionLocal() as db:
        await seed(db)

    # Launch bot polling in the background
    from app.bot_runner import create_bot, create_dispatcher

    bot = create_bot()
    dp = create_dispatcher()

    async def _polling():
        try:
            await dp.start_polling(bot, allowed_updates=["message", "callback_query"])
        except Exception as exc:
            logger.exception("Bot polling error: %s", exc)

    asyncio.create_task(_polling())
    logger.info("Telegram bot polling started.")




@app.get("/", tags=["health"])
async def health_check() -> dict:
    return {"status": "ok", "service": "church-game"}
