from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterable, List, Optional

from .config import settings
from .models import CommitInput, RiskReport

SCHEMA = """
CREATE TABLE IF NOT EXISTS commits (
    sha           TEXT PRIMARY KEY,
    repo_path     TEXT NOT NULL,
    author        TEXT,
    author_email  TEXT,
    timestamp     TEXT,
    message       TEXT,
    files_json    TEXT
);

CREATE TABLE IF NOT EXISTS reports (
    sha             TEXT,
    prompt_version  TEXT,
    model_version   TEXT,
    risk_score      INTEGER,
    risk_band       TEXT,
    summary         TEXT,
    action          TEXT,
    skipped         INTEGER,
    payload_json    TEXT,
    created_at      TEXT,
    PRIMARY KEY (sha, prompt_version, model_version)
);

CREATE INDEX IF NOT EXISTS idx_reports_score ON reports(risk_score DESC);
CREATE INDEX IF NOT EXISTS idx_reports_created ON reports(created_at DESC);
"""


@contextmanager
def connect(db_path: Optional[Path] = None):
    path = Path(db_path or settings.db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    try:
        conn.executescript(SCHEMA)
        yield conn
        conn.commit()
    finally:
        conn.close()


def upsert_commit(conn: sqlite3.Connection, repo_path: str, c: CommitInput) -> None:
    conn.execute(
        """INSERT OR REPLACE INTO commits (sha, repo_path, author, author_email, timestamp, message, files_json)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (
            c.sha, repo_path, c.author, c.author_email,
            c.timestamp.isoformat(), c.message,
            json.dumps([f.model_dump() for f in c.files]),
        ),
    )


def save_report(conn: sqlite3.Connection, r: RiskReport) -> None:
    conn.execute(
        """INSERT OR REPLACE INTO reports
           (sha, prompt_version, model_version, risk_score, risk_band, summary, action, skipped, payload_json, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            r.sha, r.prompt_version, r.model_version,
            r.risk_score, r.risk_band.value, r.summary, r.recommended_action,
            1 if r.skipped_deep_analysis else 0,
            r.model_dump_json(),
            r.created_at.isoformat(),
        ),
    )


def get_report(conn: sqlite3.Connection, sha: str) -> Optional[RiskReport]:
    row = conn.execute(
        "SELECT payload_json FROM reports WHERE sha = ? ORDER BY created_at DESC LIMIT 1",
        (sha,),
    ).fetchone()
    if not row:
        return None
    return RiskReport.model_validate_json(row["payload_json"])


def list_reports(conn: sqlite3.Connection, limit: int = 100) -> List[RiskReport]:
    rows = conn.execute(
        "SELECT payload_json FROM reports ORDER BY risk_score DESC, created_at DESC LIMIT ?",
        (limit,),
    ).fetchall()
    return [RiskReport.model_validate_json(r["payload_json"]) for r in rows]


def commit_already_scored(conn: sqlite3.Connection, sha: str, prompt_version: str, model_version: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM reports WHERE sha=? AND prompt_version=? AND model_version=?",
        (sha, prompt_version, model_version),
    ).fetchone()
    return row is not None
