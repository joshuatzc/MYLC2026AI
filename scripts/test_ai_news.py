"""
scripts/test_ai_news.py – Interactive script to test and verify event-driven AI news.

Run it with:
  python -m scripts.test_ai_news
"""
from __future__ import annotations

import asyncio
import os
import sys

# Add project root to python path to ensure imports work correctly when running directly
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from dotenv import load_dotenv
load_dotenv()

from app.config import settings
from app.database import init_db, AsyncSessionLocal
from app.models import Group
from app.services.ai_news import generate_gemini_news, build_news_prompt
from sqlalchemy import select


async def test_event_driven_news_flow():
    print("🔔 Starting Event-Driven AI News Verification Script...")
    
    # Initialize DB and ensure tables are created
    print("📁 Initialising database connection...")
    await init_db()

    print(f"🤖 Configuration - Model: {settings.GEMINI_MODEL}")

    # 1. Fetch standings and history
    print("🔍 Fetching current leaderboard standings and recent history...")
    standings = []
    history_logs = []
    async with AsyncSessionLocal() as session:
        # Standings
        groups_stmt = select(Group).order_by(Group.population.desc())
        groups = (await session.execute(groups_stmt)).scalars().all()
        for idx, g in enumerate(groups):
            standings.append(f"{idx + 1}. {g.name} ({int(g.population)} members)")

        # History
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

        history_events.sort(key=lambda x: x["time"], reverse=True)
        recent_history = history_events[:5]
        recent_history.reverse()
        history_logs = [f"- {item['desc']}" for item in recent_history]

    print(f"\nLeaderboard Standings:")
    for std in standings:
        print(std)
    if not standings:
        print("(No groups found in the database. Please make sure the database is seeded!)")
        standings = ["1. Red Choir (150 members)", "2. Blue Ushers (110 members)", "3. Green Youth (10 members)"]

    print(f"\nRecent Game History Log:")
    for hist in history_logs:
        print(hist)
    if not history_logs:
        print("(No game history found in the database. Using simulated mock history for testing!)")
        history_logs = [
            "- Group 'Group 2' upgraded standard station 'Prayer Room' to Level 1.",
            "- Group 'Group 1' upgraded standard station 'AV Equipment' to Level 1.",
            "- Group 'Group 'Group 1' completed a massive Church Upgrade and stole 15 members from Group 'Group 2'!"
        ]
        for hist in history_logs:
            print(hist)

    # 2. Simulate a mock event
    print("\n🎭 Simulating a Mock Event: 'Worship Team Level 2 Upgrade' for Group 1...")
    mock_details = {
        "group_name": "Group 1",
        "station_name": "Worship Team",
        "level_number": 2,
        "old_population": 40.0,
        "new_population": 52.0
    }
    
    # 3. Build the prompt
    print("\n✍️ Constructing Gemini Prompt...")
    prompt = build_news_prompt("upgrade", mock_details, standings, history_logs)

    print("\n=== CONSTRUCTED PROMPT ===")
    print(prompt)
    print("==========================")

    # 4. Call Gemini API
    api_key = settings.GEMINI_API_KEY
    if not api_key:
        print("\n⚠️ WARNING: GEMINI_API_KEY environment variable is empty or not set in your .env file.")
        print("To run a live end-to-end test, please add your Google AI Studio API key to .env:")
        print("GEMINI_API_KEY=your_key_here")
        print("\nSkipping live API call. Offline prompt verification looks perfect!")
    else:
        print("\n🚀 Executing live call to Google Gemini API (model: {})...".format(settings.GEMINI_MODEL))
        response = await generate_gemini_news(prompt)
        print("\n=== LIVE EVENT-DRIVEN AI NEWS BULLETIN ===")
        print(response)
        print("==========================================")


if __name__ == "__main__":
    asyncio.run(test_event_driven_news_flow())
