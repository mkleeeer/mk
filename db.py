import re
import sqlite3
from datetime import date, datetime
from pathlib import Path

DB_PATH = Path(__file__).parent / "registry.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    title TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS images (
    id TEXT PRIMARY KEY,
    job_id TEXT NOT NULL,
    seq INTEGER NOT NULL,
    filename TEXT NOT NULL,
    local_path TEXT NOT NULL,
    original_path TEXT,
    source_url TEXT NOT NULL,
    source_page TEXT,
    title TEXT,
    caption TEXT,
    mime_type TEXT,
    width INTEGER,
    height INTEGER,
    created_at TEXT NOT NULL,
    drive_file_id TEXT,
    drive_url TEXT,
    FOREIGN KEY (job_id) REFERENCES jobs (id)
);
"""


def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with get_conn() as conn:
        conn.executescript(SCHEMA)


def slugify(text: str) -> str:
    text = text.strip().lower()
    text = re.sub(r"[^\w\-가-힣]+", "_", text)
    return text.strip("_")[:80] or "job"


def get_or_create_job(folder: str | None, title: str | None = None) -> str:
    job_id = slugify(folder) if folder else f"{date.today():%Y-%m-%d}_inbox"
    with get_conn() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO jobs (id, title, created_at) VALUES (?, ?, ?)",
            (job_id, title or folder or "Inbox", datetime.now().isoformat(timespec="seconds")),
        )
    return job_id


def next_job_seq(job_id: str) -> int:
    with get_conn() as conn:
        row = conn.execute("SELECT COUNT(*) AS n FROM images WHERE job_id = ?", (job_id,)).fetchone()
    return row["n"] + 1


def next_daily_seq() -> int:
    today = date.today().isoformat()
    with get_conn() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM images WHERE substr(created_at, 1, 10) = ?", (today,)
        ).fetchone()
    return row["n"] + 1


def insert_image(record: dict) -> None:
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO images
               (id, job_id, seq, filename, local_path, original_path, source_url, source_page,
                title, caption, mime_type, width, height, created_at, drive_file_id, drive_url)
               VALUES (:id, :job_id, :seq, :filename, :local_path, :original_path, :source_url, :source_page,
                       :title, :caption, :mime_type, :width, :height, :created_at, :drive_file_id, :drive_url)""",
            record,
        )


def update_image_drive(image_id: str, drive_file_id: str, drive_url: str) -> None:
    with get_conn() as conn:
        conn.execute(
            "UPDATE images SET drive_file_id = ?, drive_url = ? WHERE id = ?",
            (drive_file_id, drive_url, image_id),
        )


def get_image(image_id: str) -> dict | None:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM images WHERE id = ?", (image_id,)).fetchone()
    return dict(row) if row else None


def get_job(job_id: str) -> dict | None:
    with get_conn() as conn:
        job_row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        if not job_row:
            return None
        image_rows = conn.execute(
            "SELECT * FROM images WHERE job_id = ? ORDER BY seq ASC", (job_id,)
        ).fetchall()
    job = dict(job_row)
    job["images"] = [dict(r) for r in image_rows]
    return job
