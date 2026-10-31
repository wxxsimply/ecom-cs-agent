from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_compose_uses_named_volume_for_data():
    compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    assert "mewhelp_data:/app/dbdata" in compose
    assert "APP_DB_PATH: /app/dbdata/app.db" in compose
    assert "CHECKPOINT_DB: /app/dbdata/checkpoints.db" in compose
    assert "KNOWLEDGE_DIR: /app/data/knowledge" in compose
    assert "./data:/app/data" not in compose


def test_dockerignore_excludes_databases_but_keeps_knowledge():
    ignore = (ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines()
    assert "app.db" in ignore
    assert "*.db" in ignore
    assert "data/checkpoints.db*" in ignore
    assert not any(line.startswith("data/knowledge") for line in ignore)
    assert "mkdir -p /app/dbdata" in (ROOT / "Dockerfile").read_text(encoding="utf-8")
