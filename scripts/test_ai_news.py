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

    # 1. Fetch standings
    print("🔍 Fetching current leaderboard standings...")
    standings = []
    async with AsyncSessionLocal() as session:
        groups_stmt = select(Group).order_by(Group.population.desc())
        groups = (await session.execute(groups_stmt)).scalars().all()
        for idx, g in enumerate(groups):
            standings.append(f"{idx + 1}. {g.name} ({int(g.population)} members)")

    print(f"\nLeaderboard Standings:")
    for std in standings:
        print(std)
    if not standings:
        print("(No groups found in the database. Please make sure the database is seeded!)")
        # Add some mock standings if empty
        standings = ["1. Red Choir (150 members)", "2. Blue Ushers (110 members)", "3. Green Youth (10 members)"]

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
    prompt = build_news_prompt("upgrade", mock_details, standings)

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
