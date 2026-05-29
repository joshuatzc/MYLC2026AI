"""
services/ai_news.py – AI news generation and broadcasting service.

Queries recent game activities (upgrades and steals) and leaderboard standings,
prompts the Google Gemini 3.1 Flash-Lite API for a creative church news broadcast,
and broadcasts the resulting news to all active Telegram chat sessions.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta
import aiohttp
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.config import settings
from app.database import AsyncSessionLocal
from app.models import ChatState, Group, GroupStationProgress, StationLevel, Station, StealRecord

logger = logging.getLogger(__name__)


async def generate_gemini_news(prompt: str) -> str:
    """
    Sends a prompt to the Google Gemini API using aiohttp and returns the generated text.
    Uses the configured Gemini model (defaults to gemini-3.1-flash-lite) and API key.
    """
    api_key = settings.GEMINI_API_KEY
    model = settings.GEMINI_MODEL

    if not api_key:
        logger.warning("GEMINI_API_KEY is not set. Skipping AI news generation.")
        return "📻 *Static on the radio...* The church broadcast system has no power (GEMINI_API_KEY is missing)."

    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"
    payload = {
        "contents": [
            {
                "parts": [
                    {"text": prompt}
                ]
            }
        ],
        "generationConfig": {
            "temperature": 0.8,
            "maxOutputTokens": 250,
        }
    }

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=payload, timeout=20) as response:
                if response.status == 200:
                    data = await response.json()
                    try:
                        text = data["candidates"][0]["content"]["parts"][0]["text"]
                        return text.strip()
                    except (KeyError, IndexError) as exc:
                        logger.error("Failed to parse Gemini response payload: %s. Response: %s", exc, data)
                        return "📻 *Static...* (AI broadcast received but unintelligible)."
                else:
                    error_text = await response.text()
                    logger.error("Gemini API call failed with status %d: %s", response.status, error_text)
                    return f"📻 *Static...* (Gemini broadcast failed: status {response.status})."
    except Exception as exc:
        logger.exception("Error calling Gemini API: %s", exc)
        return "📻 *Static...* (Broadcast system connection interrupted)."


async def get_recent_activity_and_standings(interval_minutes: int) -> tuple[list[str], list[str]]:
    """
    Queries the database for:
    1. Upgrades completed in the last `interval_minutes`.
    2. Steals committed in the last `interval_minutes`.
    3. The current leaderboard (population rank).
    """
    cutoff = datetime.utcnow() - timedelta(minutes=interval_minutes)
    activity = []
    standings = []

    async with AsyncSessionLocal() as session:
        # 1. Fetch upgrades
        progress_stmt = (
            select(GroupStationProgress)
            .where(GroupStationProgress.completed_at >= cutoff)
            .options(
                selectinload(GroupStationProgress.group),
                selectinload(GroupStationProgress.station_level).selectinload(StationLevel.station)
            )
            .order_by(GroupStationProgress.completed_at.desc())
        )
        progress_records = (await session.execute(progress_stmt)).scalars().all()

        for p in progress_records:
            group_name = p.group.name if p.group else "Unknown Group"
            station_name = p.station_level.station.name if p.station_level and p.station_level.station else "Unknown Station"
            level_num = p.station_level.level_number if p.station_level else 0
            pop_after = int(p.population_after)
            activity.append(
                f"- Group '{group_name}' successfully upgraded standard station '{station_name}' to Level {level_num} "
                f"(new population: {pop_after})."
            )

        # 2. Fetch steals
        steal_stmt = (
            select(StealRecord)
            .where(StealRecord.created_at >= cutoff)
            .options(
                selectinload(StealRecord.stealer_group),
                selectinload(StealRecord.target_group)
            )
            .order_by(StealRecord.created_at.desc())
        )
        steal_records = (await session.execute(steal_stmt)).scalars().all()

        for s in steal_records:
            stealer = s.stealer_group.name if s.stealer_group else "Unknown Group"
            target = s.target_group.name if s.target_group else "Unknown Group"
            amount = int(s.amount)
            activity.append(
                f"- Group '{stealer}' completed a Church Upgrade and stole {amount} congregation members from Group '{target}'!"
            )

        # 3. Fetch standings
        groups_stmt = select(Group).order_by(Group.population.desc())
        groups = (await session.execute(groups_stmt)).scalars().all()
        for idx, g in enumerate(groups):
            standings.append(f"{idx + 1}. {g.name} ({int(g.population)} members)")

    return activity, standings


def build_news_prompt(activity: list[str], standings: list[str], interval_minutes: int) -> str:
    """
    Constructs the prompt detailing system expectations, game activities, and standings.
    """
    system_instructions = (
        "You are 'The MYLC Chronicle', a witty, dramatic, and humorous AI news anchor reporting on "
        "a competitive church-building game. Your tone is like a lively parish radio announcer mixed with "
        "a dramatic sports caster. You love church-themed puns, gentle teasing of lagging groups, and epic "
        "descriptions of achievements.\n\n"
        "Generate a highly entertaining, creative, and dynamic broadcast summary of the last period. "
        "Rules:\n"
        "1. Write exactly 2 to 4 sentences.\n"
        "2. Keep the total word count under 100 words.\n"
        "3. Emphasize actual database activities (upgrades/steals) if they occurred.\n"
        "4. If NO activity occurred, create a humorous piece of fictional church gossip (e.g., Pastor falling asleep, "
        "cookies stolen from coffee hour, choir rehearsal drama) referencing the leading group or current standings.\n"
        "5. Format the message clearly with a starting radio emoji (e.g. 📻 **THE MYLC CHRONICLE**).\n"
        "6. Do not include markdown code blocks or raw JSON in your output; output plain text ready for Telegram with basic bold/italic tags."
    )

    if activity:
        recent_log = "\n".join(activity)
    else:
        recent_log = f"No activity occurred in the last {interval_minutes} minutes."

    leaderboard_log = "\n".join(standings)

    prompt = (
        f"{system_instructions}\n\n"
        f"--- GAME DATA FOR THE LAST {interval_minutes} MINUTES ---\n"
        f"Recent Activities:\n{recent_log}\n\n"
        f"Current Leaderboard Standings:\n{leaderboard_log}\n"
    )
    return prompt


async def run_periodic_news_broadcast(bot) -> None:
    """
    Background worker loop that triggers AI news updates periodically.
    Runs indefinitely on FastAPI startup.
    """
    logger.info("AI news periodic broadcast worker started.")

    while True:
        try:
            # Load interval dynamically in case it is changed / reloaded
            interval_mins = settings.AI_NEWS_INTERVAL_MINUTES
            interval_secs = interval_mins * 60

            # Wait for the next interval
            logger.info("AI news worker sleeping for %d seconds (%d minutes)...", interval_secs, interval_mins)
            await asyncio.sleep(interval_secs)

            logger.info("Executing scheduled AI news broadcast...")

            # 1. Fetch data
            activity, standings = await get_recent_activity_and_standings(interval_mins)

            # 2. Build prompt
            prompt = build_news_prompt(activity, standings, interval_mins)

            # 3. Call Gemini
            news_text = await generate_gemini_news(prompt)

            # 4. Broadcast to all active chat sessions
            async with AsyncSessionLocal() as session:
                chat_ids_stmt = select(ChatState.chat_id)
                chat_ids = (await session.execute(chat_ids_stmt)).scalars().all()

            if not chat_ids:
                logger.info("No active chat sessions found in database to broadcast to.")
                continue

            logger.info("Broadcasting news bulletin to %d chat sessions...", len(chat_ids))
            for chat_id in chat_ids:
                try:
                    await bot.send_message(chat_id=chat_id, text=news_text)
                    # Tiny sleep to avoid hitting Telegram's rate limits
                    await asyncio.sleep(0.05)
                except Exception as chat_exc:
                    logger.warning("Failed to send broadcast message to chat %s: %s", chat_id, chat_exc)

        except asyncio.CancelledError:
            logger.info("AI news worker task was cancelled.")
            break
        except Exception as exc:
            logger.exception("Unexpected error in periodic news worker loop: %s", exc)
            # Sleep a bit before retrying to avoid spamming errors if DB or network goes down
            await asyncio.sleep(30)
