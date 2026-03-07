"""SQLite job tracker for batch processing."""

import sqlite3
from datetime import datetime
from pathlib import Path


def _get_conn(db_path: str) -> sqlite3.Connection:
    """Get a connection, creating the table if needed."""
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute(
        """CREATE TABLE IF NOT EXISTS jobs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            input_file TEXT NOT NULL,
            patient_id TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            step_failed TEXT,
            error_msg TEXT,
            retries INTEGER DEFAULT 0,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )"""
    )
    conn.commit()
    return conn


def create_job(db_path: str, input_file: str, patient_id: str) -> int:
    """Create a new job. Returns the job ID."""
    now = datetime.now().isoformat()
    conn = _get_conn(db_path)
    cursor = conn.execute(
        "INSERT INTO jobs (input_file, patient_id, status, created_at, updated_at) VALUES (?, ?, 'pending', ?, ?)",
        (input_file, patient_id, now, now),
    )
    conn.commit()
    job_id = cursor.lastrowid
    conn.close()
    return job_id


def update_job(
    db_path: str,
    job_id: int,
    status: str,
    step_failed: str | None = None,
    error_msg: str | None = None,
) -> None:
    """Update a job's status."""
    now = datetime.now().isoformat()
    conn = _get_conn(db_path)
    conn.execute(
        "UPDATE jobs SET status=?, step_failed=?, error_msg=?, updated_at=? WHERE id=?",
        (status, step_failed, error_msg, now, job_id),
    )
    if status == "failed":
        conn.execute("UPDATE jobs SET retries = retries + 1 WHERE id=?", (job_id,))
    conn.commit()
    conn.close()


def get_pending_jobs(db_path: str) -> list[dict]:
    """Get all pending jobs."""
    conn = _get_conn(db_path)
    rows = conn.execute("SELECT * FROM jobs WHERE status = 'pending'").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_failed_jobs(db_path: str) -> list[dict]:
    """Get all failed jobs."""
    conn = _get_conn(db_path)
    rows = conn.execute("SELECT * FROM jobs WHERE status = 'failed'").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_all_jobs(db_path: str) -> list[dict]:
    """Get all jobs."""
    conn = _get_conn(db_path)
    rows = conn.execute("SELECT * FROM jobs ORDER BY id").fetchall()
    conn.close()
    return [dict(r) for r in rows]
