"""
config.py – centralised settings, loaded from environment variables or a .env file.
"""
import os
from dotenv import load_dotenv

load_dotenv()


class Settings:
    # --- Telegram ---
    TELEGRAM_BOT_TOKEN: str = os.getenv("TELEGRAM_BOT_TOKEN", "YOUR_BOT_TOKEN_HERE")

    # --- Auth ---
    LEADER_PASSWORD: str = os.getenv("LEADER_PASSWORD", "changeme")

    # --- Database ---
    DATABASE_URL: str = os.getenv(
        "DATABASE_URL", "sqlite+aiosqlite:///./church_game.db"
    )

    # --- FastAPI ---
    API_HOST: str = os.getenv("API_HOST", "0.0.0.0")
    API_PORT: int = int(os.getenv("API_PORT", "8000"))
    SECRET_KEY: str = os.getenv("SECRET_KEY", "change-me-in-production")

    # --- Game defaults ---
    STARTING_POPULATION: int = 10

    # --- Gemini & AI News ---
    GEMINI_API_KEY: str = os.getenv("GEMINI_API_KEY", "")
    GEMINI_MODEL: str = os.getenv("GEMINI_MODEL", "gemini-3.1-flash-lite")



settings = Settings()

