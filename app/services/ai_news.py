"""
services/ai_news.py – Event-driven AI news generation and broadcasting service.

Triggered immediately upon upgrades, renames, steals, or group creations.
Prompts Google Gemini 3.1 Flash-Lite to generate creative bulletins and
broadcasts them directly to all active Telegram chat sessions in the background.
"""
from __future__ import annotations

import asyncio
import logging
import aiohttp
from sqlalchemy import select

from app.config import settings
from app.database import AsyncSessionLocal
from app.models import ChatState, Group

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


def build_news_prompt(event_type: str, details: dict, standings: list[str], history_logs: list[str], tone: str | None = None) -> str:
    """
    Constructs the dynamic prompt detailing expectations, the event, recent history, and standings.
    """
    if not tone:
        import random
        tone = random.choice(["sarcastic", "encouraging", "hype"])

    if tone == "encouraging":
        tone_rule = (
            "For Section 3 (Implications / Commentary), you MUST write in a highly encouraging, positive, warm, and supportive tone. "
            "Celebrate the leading group's hard work, cheer on the lagging groups to keep fighting and building, and inspire a sense "
            "of friendly fellowship, hope, and community growth. Avoid any sarcasm, mockery, or cynicism in this section."
        )
    elif tone == "hype":
        tone_rule = (
            "For Section 3 (Implications / Commentary), you MUST write in an epic, highly dramatic, and high-energy sports commentator tone. "
            "Hype up the rising stakes, paint the leading group as a mighty giant, build up the suspense and leaderboard rivalry, and make "
            "it feel like a grand, action-packed showdown. Avoid sarcastic roasting; focus on high-stakes competition."
        )
    else:
        tone_rule = (
            "For Section 3 (Implications / Commentary), you MUST write in a highly sarcastic, dryly witty, and cheeky tone. "
            "Playfully roast the slacking groups who are stuck at their starting populations or napping in the back pews, comparing them "
            "to statues and teasing them for falling behind."
        )

    system_instructions = (
        "You are 'The MYLC TIMES', a witty, cheeky, and high-energy AI news anchor reporting on "
        "a competitive church-building game. Your tone is a natural, authentic blend of a dramatic sports commentator "
        "and a cheeky news reporter. Keep it realistic, witty, and engaging.\n\n"
        "Generate a highly entertaining, creative broadcast summary of the event that just occurred and its leaderboard impact.\n\n"
        "Writing Style & Tone Rules:\n"
        "1. Open the event summary dynamically with a diverse set of gossip or rumor-starting phrases (e.g., 'Hearsay...', 'I just heard...', 'Listen up guys...', 'Apparently...', 'Word on the street is...'). Do NOT always start with the same word.\n"
        "2. Make clever, safe, and humorous wordplay or puns based on the group's name, depending on their performance or leaderboard standing (e.g., if a group named 'United' is winning, write 'they truly are united!', but if they are losing, write 'are they really united?').\n"
        "3. Look closely at the RECENT GAME HISTORY section to notice patterns, streaks, or repeating actions (e.g., a group doing multiple upgrades in a row, or stealing repeatedly from the same rival) and reference these rivalries or momentum hilariously in your commentary.\n"
        "4. Do NOT use any em-dashes (—) or colons (:) in the entire output.\n"
        "5. Use raw digits for all numbers, populations, and level numbers (e.g., write 'Level 2' instead of 'rank two' or 'two', and '52' instead of 'fifty two' or 'fifty-two'). Do NOT spell out numbers as words.\n"
        "6. Whenever referring to any specific group names in the event or standings, always bold them using HTML tags (e.g., write '<b>Group 1</b>' or '<b>good church</b>').\n"
        "7. Drop a few expressive, relevant reaction emojis here and there naturally throughout the message to make the bulletin visually engaging for Telegram (prefer reaction/human emojis like strong biceps 💪, laughing/crying-laughing 😂/🤣, eyes 👀, shushing 🤫, shocked 😱, fire 🔥, or celebrating 🎉 instead of item emojis like hammers or churches, and do not oversaturate it).\n"
        f"8. MANDATORY IMPLICATIONS TONE RULE: {tone_rule}\n\n"
        "Formatting Rules:\n"
        "1. Write the message in exactly three sections separated by single blank lines (two newlines):\n"
        "   - Section 1 (Title): Strictly output '📻 <b>THE MYLC TIMES</b>'\n"
        "   - Section 2 (Actual Event): Summarize what just happened in a realistic, witty style with the dynamic opening.\n"
        "   - Section 3 (Implications / Commentary): Discuss the leaderboard standings, group progress, or other groups' reactions based on the MANDATORY IMPLICATIONS TONE RULE.\n"
        "2. Do NOT use markdown asterisks (**) for bolding. Use HTML bold tags (<b>...</b>) for any bolding to ensure Telegram parses it correctly.\n"
        "3. Keep the total message under 100 words."
    )

    event_desc = ""
    if event_type == "upgrade":
        event_desc = (
            f"Group '{details['group_name']}' successfully completed standard upgrade "
            f"'{details['station_name']}' Level {details['level_number']}! "
            f"Their population increased: {int(details['old_population'])} -> {int(details['new_population'])}."
        )
    elif event_type == "church_upgrade":
        steal_info = ""
        if details.get("theft_applied"):
            steal_info = f" They also successfully stole {int(details['stolen_amount'])} congregation members from Group '{details['target_name']}'!"
        event_desc = (
            f"Group '{details['group_name']}' completed a massive Church Upgrade to Level {details['level_number']} "
            f"({details['tier_name']})! Their population capacity expands. Population: {int(details['old_population'])} -> {int(details['new_population'])}.{steal_info}"
        )
    elif event_type == "rename":
        event_desc = (
            f"Group '{details['old_name']}' has officially renamed their church to '{details['new_name']}'!"
        )
    elif event_type == "create_group":
        event_desc = (
            f"A brand new group named '{details['group_name']}' has officially entered the church-building race with an initial population of {int(details['population'])}!"
        )
    else:
        event_desc = f"An administrative event occurred for Group '{details.get('group_name', 'Unknown')}': {details.get('description', 'Status updated')}."

    standings_str = "\n".join(standings)
    history_str = "\n".join(history_logs) if history_logs else "No game history recorded yet."

    prompt = (
        f"{system_instructions}\n\n"
        f"--- RECENT EVENT ---\n"
        f"{event_desc}\n\n"
        f"--- RECENT GAME HISTORY (OLDEST TO NEWEST) ---\n"
        f"{history_str}\n\n"
        f"--- CURRENT LEADERBOARD STANDINGS ---\n"
        f"{standings_str}\n"
    )
    return prompt


async def trigger_event_broadcast(event_type: str, details: dict) -> None:
    """
    Triggers an immediate, event-driven AI news broadcast based on a game or admin action.
    This is run as a non-blocking background task.
    """
    logger.info("Triggered event-driven news broadcast for event type: %s", event_type)

    try:
        # 1. Fetch current standings and recent history
        standings = []
        history_logs = []
        async with AsyncSessionLocal() as session:
            # Standings
            groups_stmt = select(Group).order_by(Group.population.desc())
            groups = (await session.execute(groups_stmt)).scalars().all()
            for idx, g in enumerate(groups):
                standings.append(f"{idx + 1}. {g.name} ({int(g.population)} members)")

            # Recent History: Last 5 standard completions
            from app.models import GroupStationProgress, StealRecord, StationLevel, Station
            from sqlalchemy.orm import selectinload

            progress_stmt = (
                select(GroupStationProgress)
                .options(
                    selectinload(GroupStationProgress.group),
                    selectinload(GroupStationProgress.station_level).selectinload(StationLevel.station)
                )
                .order_by(GroupStationProgress.completed_at.desc())
                .limit(5)
            )
            progress_records = (await session.execute(progress_stmt)).scalars().all()

            # Recent History: Last 5 steals
            steal_stmt = (
                select(StealRecord)
                .options(
                    selectinload(StealRecord.stealer_group),
                    selectinload(StealRecord.target_group)
                )
                .order_by(StealRecord.created_at.desc())
                .limit(5)
            )
            steal_records = (await session.execute(steal_stmt)).scalars().all()

            # Compile history events
            history_events = []
            for p in progress_records:
                history_events.append({
                    "time": p.completed_at,
                    "desc": f"Group '{p.group.name if p.group else 'Unknown Group'}' upgraded '{p.station_level.station.name if p.station_level and p.station_level.station else 'Unknown Station'}' to Level {p.station_level.level_number if p.station_level else 0}."
                })
            for s in steal_records:
                history_events.append({
                    "time": s.created_at,
                    "desc": f"Group '{s.stealer_group.name if s.stealer_group else 'Unknown Group'}' upgraded their Church and stole {int(s.amount)} members from Group '{s.target_group.name if s.target_group else 'Unknown Group'}'."
                })

            # Sort combined history chronologically (oldest to newest)
            history_events.sort(key=lambda x: x["time"], reverse=True)
            recent_history = history_events[:5]
            recent_history.reverse()
            history_logs = [f"- {item['desc']}" for item in recent_history]

        # 2. Build prompt
        import random
        selected_tone = random.choice(["sarcastic", "encouraging", "hype"])
        prompt = build_news_prompt(event_type, details, standings, history_logs, tone=selected_tone)

        # 4. Generate AI summary
        news_text = await generate_gemini_news(prompt)

        # 5. Broadcast to all active chat sessions
        from aiogram import Bot
        from aiogram.client.default import DefaultBotProperties
        from aiogram.enums import ParseMode

        bot = Bot(token=settings.TELEGRAM_BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
        try:
            async with AsyncSessionLocal() as session:
                chat_ids_stmt = select(ChatState.chat_id)
                chat_ids = (await session.execute(chat_ids_stmt)).scalars().all()

            if not chat_ids:
                logger.info("No active chat sessions found in database to broadcast to.")
                return

            logger.info("Broadcasting event-driven news to %d chat sessions...", len(chat_ids))
            for chat_id in chat_ids:
                try:
                    await bot.send_message(chat_id=chat_id, text=news_text)
                    await asyncio.sleep(0.05)
                except Exception as chat_exc:
                    logger.warning("Failed to send broadcast message to chat %s: %s", chat_id, chat_exc)
        finally:
            await bot.session.close()

    except Exception as exc:
        logger.exception("Unexpected error in event broadcast task: %s", exc)
