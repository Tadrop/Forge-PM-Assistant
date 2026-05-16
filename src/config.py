"""Centralized settings — env vars resolved once at import time via dotenv.

Kept as a frozen dataclass (no pydantic-settings dependency) so the
config surface is auditable and immutable.
"""
from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


def _bool(name: str, default: bool = False) -> bool:
    v = os.getenv(name)
    if v is None:
        return default
    return v.strip().lower() in {"1", "true", "yes", "on"}


def _int(name: str, default: int) -> int:
    v = os.getenv(name)
    if v is None or v == "":
        return default
    try:
        return int(v)
    except ValueError:
        return default


def _float(name: str, default: float) -> float:
    v = os.getenv(name)
    if v is None or v == "":
        return default
    try:
        return float(v)
    except ValueError:
        return default


@dataclass(frozen=True)
class Settings:
    # ClickUp
    clickup_api_token: str
    clickup_team_id: str

    # Claude
    anthropic_api_key: str
    anthropic_model: str

    # Pinecone
    pinecone_api_key: str
    pinecone_index: str
    pinecone_cloud: str
    pinecone_region: str

    # Voyage embeddings
    voyage_api_key: str
    voyage_model: str

    # Slack
    slack_bot_token: str
    slack_leadership_channel: str
    slack_pm_review_channel: str

    # Gmail
    gmail_credentials_path: str
    gmail_token_path: str

    # Behavior
    stuck_blocker_days: int
    untouched_days: int
    scope_similarity_threshold: float
    scope_rag_top_k: int
    tz: str
    dry_run: bool


def load_settings() -> Settings:
    return Settings(
        clickup_api_token=os.getenv("CLICKUP_API_TOKEN", ""),
        clickup_team_id=os.getenv("CLICKUP_TEAM_ID", ""),
        anthropic_api_key=os.getenv("ANTHROPIC_API_KEY", ""),
        anthropic_model=os.getenv("ANTHROPIC_MODEL", "claude-opus-4-7"),
        pinecone_api_key=os.getenv("PINECONE_API_KEY", ""),
        pinecone_index=os.getenv("PINECONE_INDEX", "forge-sows"),
        pinecone_cloud=os.getenv("PINECONE_CLOUD", "aws"),
        pinecone_region=os.getenv("PINECONE_REGION", "us-east-1"),
        voyage_api_key=os.getenv("VOYAGE_API_KEY", ""),
        voyage_model=os.getenv("VOYAGE_MODEL", "voyage-3-lite"),
        slack_bot_token=os.getenv("SLACK_BOT_TOKEN", ""),
        slack_leadership_channel=os.getenv("SLACK_LEADERSHIP_CHANNEL", "#leadership"),
        slack_pm_review_channel=os.getenv("SLACK_PM_REVIEW_CHANNEL", "#pm-review"),
        gmail_credentials_path=os.getenv("GMAIL_CREDENTIALS_PATH", "./.secrets/gmail_credentials.json"),
        gmail_token_path=os.getenv("GMAIL_TOKEN_PATH", "./.secrets/gmail_token.json"),
        stuck_blocker_days=_int("STUCK_BLOCKER_DAYS", 5),
        untouched_days=_int("UNTOUCHED_DAYS", 5),
        scope_similarity_threshold=_float("SCOPE_SIMILARITY_THRESHOLD", 0.72),
        scope_rag_top_k=_int("SCOPE_RAG_TOP_K", 3),
        tz=os.getenv("TZ", "America/Los_Angeles"),
        dry_run=_bool("DRY_RUN", True),
    )


settings = load_settings()
