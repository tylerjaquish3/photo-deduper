import sqlite3

SCHEMA = """
CREATE TABLE IF NOT EXISTS files (
    path TEXT PRIMARY KEY,
    size INTEGER NOT NULL,
    mtime REAL NOT NULL,
    phash TEXT NOT NULL,
    width INTEGER NOT NULL,
    height INTEGER NOT NULL,
    file_size INTEGER NOT NULL,
    date_taken TEXT NOT NULL,
    sharpness REAL NOT NULL,
    is_screenshot INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS scan_errors (
    path TEXT PRIMARY KEY,
    error TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS resolved_groups (
    group_id TEXT PRIMARY KEY,
    resolved_at TEXT NOT NULL
);
"""


def init_db(db_path):
    conn = sqlite3.connect(db_path)
    conn.executescript(SCHEMA)
    columns = {row[1] for row in conn.execute("PRAGMA table_info(files)")}
    if "is_screenshot" not in columns:
        conn.execute("ALTER TABLE files ADD COLUMN is_screenshot INTEGER NOT NULL DEFAULT 0")
        _backfill_is_screenshot(conn)
    conn.commit()
    return conn


def _backfill_is_screenshot(conn):
    # Existing cached rows predate the is_screenshot column and defaulted to
    # 0 above; fill in the real value for each without touching the
    # expensive phash/sharpness fields, so already-scanned libraries don't
    # need a full rescan to get accurate screenshot detection.
    import hashing

    for (path,) in conn.execute("SELECT path FROM files").fetchall():
        try:
            flagged = hashing.is_screenshot(path)
        except (FileNotFoundError, OSError):
            continue
        conn.execute(
            "UPDATE files SET is_screenshot = ? WHERE path = ?", (int(flagged), path)
        )


def get_cached_entry(conn, path, size, mtime):
    row = conn.execute(
        "SELECT phash, width, height, file_size, date_taken, sharpness, is_screenshot "
        "FROM files WHERE path = ? AND size = ? AND mtime = ?",
        (path, size, mtime),
    ).fetchone()
    if row is None:
        return None
    return {
        "phash": row[0],
        "width": row[1],
        "height": row[2],
        "file_size": row[3],
        "date_taken": row[4],
        "sharpness": row[5],
        "is_screenshot": bool(row[6]),
    }


def upsert_file(
    conn, path, size, mtime, phash, width, height, file_size, date_taken, sharpness, is_screenshot
):
    conn.execute(
        "INSERT INTO files "
        "(path, size, mtime, phash, width, height, file_size, date_taken, sharpness, is_screenshot) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(path) DO UPDATE SET "
        "size=excluded.size, mtime=excluded.mtime, phash=excluded.phash, "
        "width=excluded.width, height=excluded.height, file_size=excluded.file_size, "
        "date_taken=excluded.date_taken, sharpness=excluded.sharpness, "
        "is_screenshot=excluded.is_screenshot",
        (path, size, mtime, phash, width, height, file_size, date_taken, sharpness, int(is_screenshot)),
    )
    conn.commit()


def log_scan_error(conn, path, error):
    conn.execute(
        "INSERT INTO scan_errors (path, error) VALUES (?, ?) "
        "ON CONFLICT(path) DO UPDATE SET error=excluded.error",
        (path, str(error)),
    )
    conn.commit()


def all_files(conn):
    rows = conn.execute(
        "SELECT path, phash, width, height, file_size, date_taken, sharpness, is_screenshot "
        "FROM files"
    ).fetchall()
    return [
        {
            "path": row[0],
            "phash": row[1],
            "width": row[2],
            "height": row[3],
            "file_size": row[4],
            "date_taken": row[5],
            "sharpness": row[6],
            "is_screenshot": bool(row[7]),
        }
        for row in rows
    ]


def delete_file(conn, path):
    conn.execute("DELETE FROM files WHERE path = ?", (path,))
    conn.commit()


def is_group_resolved(conn, group_id):
    row = conn.execute(
        "SELECT 1 FROM resolved_groups WHERE group_id = ?", (group_id,)
    ).fetchone()
    return row is not None


def mark_group_resolved(conn, group_id):
    conn.execute(
        "INSERT OR IGNORE INTO resolved_groups (group_id, resolved_at) "
        "VALUES (?, datetime('now'))",
        (group_id,),
    )
    conn.commit()
