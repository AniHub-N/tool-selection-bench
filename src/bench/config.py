from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = ROOT / "config.toml"
RUNS_DIR = ROOT / "runs"
TASKS_DIR = ROOT / "tasks"


def load_config(path: Path | None = None) -> dict[str, Any]:
    load_dotenv(ROOT / ".env")
    with open(path or CONFIG_PATH, "rb") as f:
        return tomllib.load(f)
