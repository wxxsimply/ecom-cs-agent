from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
DB_PATH = Path(os.getenv("APP_DB_PATH", str(BASE_DIR / "app.db")))
CHECKPOINT_PATH = Path(os.getenv("CHECKPOINT_DB", str(BASE_DIR / "data" / "checkpoints.db")))
KNOWLEDGE_DIR = Path(os.getenv("KNOWLEDGE_DIR", str(DATA_DIR / "knowledge")))
LLM_MODE = os.getenv("LLM_MODE", "mock")
DEMO_USER_ID = os.getenv("DEMO_USER_ID", "u_demo")


@dataclass(frozen=True)
class Settings:
    app_name: str = "MewHelp P0-Lite"
    db_path: Path = DB_PATH
    checkpoint_path: Path = CHECKPOINT_PATH
    knowledge_dir: Path = KNOWLEDGE_DIR
    llm_mode: str = LLM_MODE
    demo_user_id: str = DEMO_USER_ID
    interrupt_ttl_seconds: int = 30 * 60
    confirm_token_ttl_seconds: int = 30 * 60
    max_input_chars: int = 2000
    max_output_tokens: int = 1500


settings = Settings()
