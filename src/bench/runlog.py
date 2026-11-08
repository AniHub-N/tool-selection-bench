"""Run directories: runs/<timestamp>_<model>_<mode>/ with config, git commit, JSONL logs.

Resume: `RunDir.start(...)` reuses the newest unfinished run with the same model,
mode and experiment config (hash of config.toml minus the [quota] section), so a
run paused by a quota can continue days later. Each session's git commit is recorded.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
import tomllib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable

from .config import CONFIG_PATH, ROOT, RUNS_DIR


def git_info() -> dict[str, Any]:
    def git(*args: str) -> str:
        return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True).stdout.strip()
    return {"commit": git("rev-parse", "HEAD") or None, "dirty": bool(git("status", "--porcelain"))}


def experiment_hash(config_path: Path = CONFIG_PATH) -> str:
    """Hash of everything that affects results. [quota] (limits, ledger path) is excluded."""
    with open(config_path, "rb") as f:
        cfg = tomllib.load(f)
    cfg.pop("quota", None)
    return hashlib.sha256(json.dumps(cfg, sort_keys=True).encode()).hexdigest()[:12]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class ResumeConflict(RuntimeError):
    pass


class RunDir:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.id = path.name

    @property
    def meta(self) -> dict[str, Any]:
        return json.loads((self.path / "meta.json").read_text())

    def _write_meta(self, meta: dict[str, Any]) -> None:
        (self.path / "meta.json").write_text(json.dumps(meta, indent=2))

    @classmethod
    def start(cls, model: str, mode: str, extra: dict[str, Any] | None = None,
              resume: bool = True, runs_dir: Path = RUNS_DIR) -> "RunDir":
        safe_model = re.sub(r"[^A-Za-z0-9.-]+", "-", model)
        h = experiment_hash()
        if resume:
            for p in sorted(runs_dir.glob(f"*_{safe_model}_{mode}"), reverse=True):
                meta = json.loads((p / "meta.json").read_text())
                if meta.get("status") == "complete":
                    continue
                if meta.get("experiment_hash") != h:
                    raise ResumeConflict(
                        f"Unfinished run {p.name} has a different experiment config. "
                        f"Finish it with the old config, or pass --new to start fresh.")
                run = cls(p)
                meta["status"] = "running"
                meta["sessions"].append({"started_at": _now(), **git_info()})
                run._write_meta(meta)
                print(f"[run] resuming {p.name} ({run.count('calls')} records so far)")
                return run
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        path = runs_dir / f"{ts}_{safe_model}_{mode}"
        n = 2
        while path.exists():  # two runs started in the same second
            path = runs_dir / f"{ts}-{n}_{safe_model}_{mode}"
            n += 1
        path.mkdir(parents=True)
        shutil.copy(CONFIG_PATH, path / "config.toml")
        run = cls(path)
        run._write_meta({"run_id": path.name, "model": model, "mode": mode, "status": "running",
                         "experiment_hash": h, "created_at": _now(),
                         "sessions": [{"started_at": _now(), **git_info()}], **(extra or {})})
        print(f"[run] new run {path.name}")
        return run

    def set_status(self, status: str, **fields: Any) -> None:
        meta = self.meta
        meta["status"] = status
        meta["sessions"][-1].update(ended_at=_now(), ended_status=status, **fields)
        self._write_meta(meta)

    def log(self, stream: str, record: dict[str, Any]) -> None:
        with open(self.path / f"{stream}.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

    def records(self, stream: str) -> Iterable[dict[str, Any]]:
        p = self.path / f"{stream}.jsonl"
        if p.exists():
            with open(p, encoding="utf-8") as f:
                for line in f:
                    if line.strip():
                        yield json.loads(line)

    def count(self, stream: str) -> int:
        return sum(1 for _ in self.records(stream))

    def done(self, stream: str, key: Callable[[dict], tuple]) -> set[tuple]:
        """Keys of work units already logged; used to skip them on resume."""
        return {key(r) for r in self.records(stream)}
