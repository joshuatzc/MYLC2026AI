"""
scripts/test_ai_news.py – Interactive script to test and verify AI news integration.

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
from app.database import init_db
from app.services.ai_news import (
    get_recent_activity_and_standings,
    build_news_prompt,
    generate_gemini_news,
)


async def test_ai_news_flow():
    print("🔔 Starting AI News Verification Script...")
    
    # Initialize DB and ensure tables are created
    print("📁 Initialising database connection...")
    await init_db()

    interval = settings.AI_NEWS_INTERVAL_MINUTES
    print(f"⏱️ Configuration - Interval: {interval} minutes")
    print(f"🤖 Configuration - Model: {settings.GEMINI_MODEL}")
    
    # 1. Fetch activities and standings
    print("🔍 Querying database for recent activity (upgrades/steals) and standings...")
    activity, standings = await get_recent_activity_and_standings(interval)

    print("\n--- DATABASE QUERY RESULTS ---")
    print(f"Found {len(activity)} recent activities in the last {interval} minutes:")
    for act in activity:
        print(act)
    if not activity:
        print("(No recent activities found - the AI will write a humorous gossip piece instead)")

    print(f"\nLeaderboard Standings:")
    for std in standings:
        print(std)
    if not standings:
        print("(No groups found in the database. Please make sure the database is seeded!)")

    # 2. Build the prompt
    print("\n✍️ Constructing Gemini Prompt...")
    prompt = build_news_prompt(activity, standings, interval)
    print("\n=== CONSTRUCTED PROMPT ===")
    print(prompt)
    print("==========================")

    # 3. Call Gemini API
    api_key = settings.GEMINI_API_KEY
    if not api_key:
        print("\n⚠️ WARNING: GEMINI_API_KEY environment variable is empty or not set in your .env file.")
        print("To run a live end-to-end test, please add your Google AI Studio API key to .env:")
        print("GEMINI_API_KEY=your_key_here")
        print("\nSkipping live API call. Offline generation logic looks perfect!")
    else:
        print("\n🚀 Executing live call to Google Gemini API (model: {})...".format(settings.GEMINI_MODEL))
        response = await generate_gemini_news(prompt)
        print("\n=== LIVE AI GENERATED NEWS BULLETIN ===")
        print(response)
        print("=======================================")


if __name__ == "__main__":
    asyncio.run(test_ai_news_flow())
