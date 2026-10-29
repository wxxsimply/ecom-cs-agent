import os
import tempfile
from pathlib import Path

ROOT = Path(tempfile.mkdtemp(prefix="p0lite-test-"))
os.environ["APP_DB_PATH"] = str(ROOT / "test.db")
os.environ["CHECKPOINT_DB"] = str(ROOT / "checkpoints.db")
os.environ["KNOWLEDGE_DIR"] = str(Path(__file__).resolve().parent.parent / "data" / "knowledge")
os.environ["DEMO_USER_ID"] = "u_demo"
