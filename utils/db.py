import sqlite3
import json
from pathlib import Path
from contextlib import contextmanager

DB_PATH = Path(__file__).parent.parent / "data" / "jobs.db"


def init_db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with get_conn() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS jobs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source TEXT NOT NULL,
                external_id TEXT,
                title TEXT NOT NULL,
                company TEXT NOT NULL,
                location TEXT,
                is_remote INTEGER DEFAULT 0,
                url TEXT UNIQUE NOT NULL,
                description TEXT,
                salary_min INTEGER,
                salary_max INTEGER,
                date_posted TEXT,
                date_found TEXT NOT NULL,
                score INTEGER,
                score_breakdown TEXT,
                score_reason TEXT,
                highlights TEXT,
                concerns TEXT,
                auto_disqualified INTEGER DEFAULT 0,
                disqualify_reason TEXT,
                status TEXT DEFAULT 'new',
                cover_letter_path TEXT,
                tailored_bullets TEXT,
                cold_outreach TEXT,
                applied_at TEXT,
                notes TEXT
            );

            CREATE TABLE IF NOT EXISTS searches (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                query TEXT,
                location TEXT,
                source TEXT,
                run_at TEXT NOT NULL,
                jobs_found INTEGER DEFAULT 0,
                jobs_new INTEGER DEFAULT 0
            );

            CREATE UNIQUE INDEX IF NOT EXISTS idx_jobs_url ON jobs(url);
            CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status);
            CREATE INDEX IF NOT EXISTS idx_jobs_score ON jobs(score);
        """)


@contextmanager
def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def insert_job(job: dict) -> bool:
    """Insert a job. Returns True if new, False if duplicate (by URL)."""
    with get_conn() as conn:
        try:
            conn.execute("""
                INSERT INTO jobs (
                    source, external_id, title, company, location, is_remote,
                    url, description, salary_min, salary_max, date_posted,
                    date_found, status
                ) VALUES (
                    :source, :external_id, :title, :company, :location, :is_remote,
                    :url, :description, :salary_min, :salary_max, :date_posted,
                    :date_found, 'new'
                )
            """, job)
            return True
        except sqlite3.IntegrityError:
            return False


def get_unscored_jobs(limit: int = 100) -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM jobs WHERE score IS NULL AND auto_disqualified = 0 LIMIT ?",
            (limit,)
        ).fetchall()
        return [dict(r) for r in rows]


def update_job_score(url: str, score_data: dict):
    with get_conn() as conn:
        status = "queued" if score_data["score"] >= 80 else \
                 "pass" if score_data["score"] < 50 else "scored"
        if score_data.get("auto_disqualify"):
            status = "rejected"

        conn.execute("""
            UPDATE jobs SET
                score = :score,
                score_breakdown = :breakdown,
                score_reason = :reasoning,
                highlights = :highlights,
                concerns = :concerns,
                auto_disqualified = :auto_disq,
                disqualify_reason = :disqualify_reason,
                status = :status
            WHERE url = :url
        """, {
            "score": score_data["score"] if not score_data.get("auto_disqualify") else 0,
            "breakdown": json.dumps(score_data.get("breakdown", {})),
            "reasoning": score_data.get("reasoning", ""),
            "highlights": json.dumps(score_data.get("highlights", [])),
            "concerns": json.dumps(score_data.get("concerns", [])),
            "auto_disq": 1 if score_data.get("auto_disqualify") else 0,
            "disqualify_reason": score_data.get("disqualify_reason"),
            "status": status,
            "url": url,
        })


def get_queued_jobs(limit: int = 50) -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM jobs WHERE status = 'queued' ORDER BY score DESC LIMIT ?",
            (limit,)
        ).fetchall()
        return [dict(r) for r in rows]


def update_job_field(url: str, field: str, value):
    allowed = {"status", "cover_letter_path", "tailored_bullets", "cold_outreach",
               "applied_at", "notes"}
    if field not in allowed:
        raise ValueError(f"Cannot update field: {field}")
    with get_conn() as conn:
        conn.execute(f"UPDATE jobs SET {field} = ? WHERE url = ?", (value, url))


def log_search(query: str, location: str, source: str, jobs_found: int, jobs_new: int):
    from datetime import datetime, timezone
    with get_conn() as conn:
        conn.execute("""
            INSERT INTO searches (query, location, source, run_at, jobs_found, jobs_new)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (query, location, source, datetime.now(timezone.utc).isoformat(),
              jobs_found, jobs_new))


def get_jobs(
    status: str | None = None,
    min_score: int | None = None,
    search: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> tuple[list[dict], int]:
    conditions = []
    params: list = []

    if status:
        statuses = status.split(",")
        placeholders = ",".join("?" * len(statuses))
        conditions.append(f"status IN ({placeholders})")
        params.extend(statuses)
    if min_score is not None:
        conditions.append("score >= ?")
        params.append(min_score)
    if search:
        conditions.append("(title LIKE ? OR company LIKE ?)")
        params.extend([f"%{search}%", f"%{search}%"])

    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""

    with get_conn() as conn:
        total = conn.execute(
            f"SELECT COUNT(*) FROM jobs {where}", params
        ).fetchone()[0]
        rows = conn.execute(
            f"SELECT * FROM jobs {where} ORDER BY score DESC NULLS LAST, date_found DESC LIMIT ? OFFSET ?",
            params + [limit, offset],
        ).fetchall()
    return [dict(r) for r in rows], total


def get_job_by_id(job_id: int) -> dict | None:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        return dict(row) if row else None


def get_stats() -> dict:
    with get_conn() as conn:
        total = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
        by_status = dict(conn.execute(
            "SELECT status, COUNT(*) FROM jobs GROUP BY status"
        ).fetchall())
        top = conn.execute(
            "SELECT title, company, score FROM jobs WHERE score IS NOT NULL "
            "ORDER BY score DESC LIMIT 10"
        ).fetchall()
        return {
            "total": total,
            "by_status": by_status,
            "top_jobs": [dict(r) for r in top],
        }
