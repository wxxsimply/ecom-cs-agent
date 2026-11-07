from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv


BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env", override=False)

DATA_DIR = BASE_DIR / "data"
DB_PATH = Path(os.getenv("APP_DB_PATH", str(BASE_DIR / "app.db")))
CHECKPOINT_PATH = Path(os.getenv("CHECKPOINT_DB", str(BASE_DIR / "data" / "checkpoints.db")))
KNOWLEDGE_DIR = Path(os.getenv("KNOWLEDGE_DIR", str(DATA_DIR / "knowledge")))
LLM_MODE = os.getenv("LLM_MODE", "mock")
DEMO_USER_ID = os.getenv("DEMO_USER_ID", "u_demo")
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")
DEEPSEEK_BASE_URL = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com").rstrip("/")
DEEPSEEK_API_STYLE = os.getenv("DEEPSEEK_API_STYLE", "auto").strip().lower()
DEEPSEEK_MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-chat")
DEEPSEEK_TIMEOUT_SECONDS = float(os.getenv("DEEPSEEK_TIMEOUT_SECONDS", "30"))
DEEPSEEK_MAX_TOKENS = int(os.getenv("DEEPSEEK_MAX_TOKENS", "1600"))
DEEPSEEK_SUMMARY_MAX_TOKENS = int(os.getenv("DEEPSEEK_SUMMARY_MAX_TOKENS", "800"))
CONTEXT_MAX_TURNS = int(os.getenv("CONTEXT_MAX_TURNS", "24"))
CONTEXT_MAX_MESSAGES = int(os.getenv("CONTEXT_MAX_MESSAGES", "48"))
CONTEXT_MAX_CHARS = int(os.getenv("CONTEXT_MAX_CHARS", "16000"))
CONTEXT_MAX_TOKENS = int(os.getenv("CONTEXT_MAX_TOKENS", "16000"))
CONTEXT_PER_MESSAGE_CHARS = int(os.getenv("CONTEXT_PER_MESSAGE_CHARS", "2000"))
SUMMARY_TRIGGER_TURNS = int(os.getenv("SUMMARY_TRIGGER_TURNS", "8"))
SUMMARY_MAX_CHARS = int(os.getenv("SUMMARY_MAX_CHARS", "2000"))


@dataclass(frozen=True)
class Settings:
    app_name: str = "MewHelp P0-Lite"
    db_path: Path = DB_PATH
    checkpoint_path: Path = CHECKPOINT_PATH
    knowledge_dir: Path = KNOWLEDGE_DIR
    llm_mode: str = LLM_MODE
    demo_user_id: str = DEMO_USER_ID
    deepseek_api_key: str = field(default=DEEPSEEK_API_KEY, repr=False)
    deepseek_base_url: str = DEEPSEEK_BASE_URL
    deepseek_api_style: str = DEEPSEEK_API_STYLE
    deepseek_model: str = DEEPSEEK_MODEL
    deepseek_timeout_seconds: float = DEEPSEEK_TIMEOUT_SECONDS
    deepseek_max_tokens: int = DEEPSEEK_MAX_TOKENS
    deepseek_summary_max_tokens: int = DEEPSEEK_SUMMARY_MAX_TOKENS
    context_max_turns: int = CONTEXT_MAX_TURNS
    context_max_messages: int = CONTEXT_MAX_MESSAGES
    context_max_chars: int = CONTEXT_MAX_CHARS
    context_max_tokens: int = CONTEXT_MAX_TOKENS
    context_per_message_chars: int = CONTEXT_PER_MESSAGE_CHARS
    summary_trigger_turns: int = SUMMARY_TRIGGER_TURNS
    summary_max_chars: int = SUMMARY_MAX_CHARS
    interrupt_ttl_seconds: int = 30 * 60
    confirm_token_ttl_seconds: int = 30 * 60
    max_input_chars: int = 2000
    max_output_tokens: int = 1500


settings = Settings()
