from __future__ import annotations

import os

from src.env_bootstrap import load_project_dotenv


def test_load_project_dotenv_sets_missing_values(tmp_path, monkeypatch) -> None:
    env_path = tmp_path / ".env"
    env_path.write_text(
        "# comment\n"
        "PLAIN=value\n"
        "QUOTED='quoted-value'\n",
        encoding="utf-8",
    )
    monkeypatch.delenv("PLAIN", raising=False)
    monkeypatch.delenv("QUOTED", raising=False)

    loaded = load_project_dotenv(env_path=env_path)

    assert loaded == env_path
    assert os.environ["PLAIN"] == "value"
    assert os.environ["QUOTED"] == "quoted-value"


def test_load_project_dotenv_preserves_existing_values(tmp_path, monkeypatch) -> None:
    env_path = tmp_path / ".env"
    env_path.write_text("PLAIN=from-dotenv\n", encoding="utf-8")
    monkeypatch.setenv("PLAIN", "existing")

    load_project_dotenv(env_path=env_path)

    assert os.environ["PLAIN"] == "existing"
