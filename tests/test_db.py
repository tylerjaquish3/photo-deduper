import sqlite3

from PIL import Image

import db


def test_init_db_creates_tables(tmp_path):
    conn = db.init_db(tmp_path / "test.db")
    tables = {
        row[0]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    assert {"files", "scan_errors", "resolved_groups"} <= tables


def test_get_cached_entry_returns_none_when_missing(tmp_path):
    conn = db.init_db(tmp_path / "test.db")
    assert db.get_cached_entry(conn, "/a.jpg", 100, 1.0) is None


def test_upsert_and_get_cached_entry(tmp_path):
    conn = db.init_db(tmp_path / "test.db")
    db.upsert_file(conn, "/a.jpg", 100, 1.0, "abc123", 800, 600, 100, "2024-01-01", 12.5, False)

    entry = db.get_cached_entry(conn, "/a.jpg", 100, 1.0)

    assert entry["phash"] == "abc123"
    assert entry["width"] == 800
    assert entry["height"] == 600
    assert entry["file_size"] == 100
    assert entry["date_taken"] == "2024-01-01"
    assert entry["sharpness"] == 12.5
    assert entry["is_screenshot"] is False


def test_get_cached_entry_misses_on_mtime_change(tmp_path):
    conn = db.init_db(tmp_path / "test.db")
    db.upsert_file(conn, "/a.jpg", 100, 1.0, "abc123", 800, 600, 100, "2024-01-01", 12.5, False)

    assert db.get_cached_entry(conn, "/a.jpg", 100, 2.0) is None


def test_upsert_overwrites_existing_row(tmp_path):
    conn = db.init_db(tmp_path / "test.db")
    db.upsert_file(conn, "/a.jpg", 100, 1.0, "old", 800, 600, 100, "2024-01-01", 12.5, False)
    db.upsert_file(conn, "/a.jpg", 200, 2.0, "new", 400, 300, 200, "2024-02-01", 5.0, True)

    assert db.get_cached_entry(conn, "/a.jpg", 100, 1.0) is None
    entry = db.get_cached_entry(conn, "/a.jpg", 200, 2.0)
    assert entry["phash"] == "new"
    assert entry["is_screenshot"] is True


def test_log_scan_error(tmp_path):
    conn = db.init_db(tmp_path / "test.db")
    db.log_scan_error(conn, "/bad.jpg", "cannot identify image file")

    row = conn.execute(
        "SELECT error FROM scan_errors WHERE path = ?", ("/bad.jpg",)
    ).fetchone()
    assert row[0] == "cannot identify image file"


def test_all_files_returns_every_row(tmp_path):
    conn = db.init_db(tmp_path / "test.db")
    db.upsert_file(conn, "/a.jpg", 100, 1.0, "hash-a", 800, 600, 100, "2024-01-01", 12.5, False)
    db.upsert_file(conn, "/b.jpg", 200, 2.0, "hash-b", 400, 300, 200, "2024-02-01", 5.0, True)

    files = db.all_files(conn)

    assert {f["path"] for f in files} == {"/a.jpg", "/b.jpg"}
    by_path = {f["path"]: f for f in files}
    assert by_path["/a.jpg"]["is_screenshot"] is False
    assert by_path["/b.jpg"]["is_screenshot"] is True


def test_delete_file_removes_row(tmp_path):
    conn = db.init_db(tmp_path / "test.db")
    db.upsert_file(conn, "/a.jpg", 100, 1.0, "hash-a", 800, 600, 100, "2024-01-01", 12.5, False)
    db.upsert_file(conn, "/b.jpg", 200, 2.0, "hash-b", 400, 300, 200, "2024-02-01", 5.0, False)

    db.delete_file(conn, "/a.jpg")

    paths = {f["path"] for f in db.all_files(conn)}
    assert paths == {"/b.jpg"}


def test_init_db_backfills_is_screenshot_for_pre_existing_rows(tmp_path):
    db_path = tmp_path / "test.db"
    shot_path = tmp_path / "shot.png"
    photo_path = tmp_path / "photo.jpg"
    Image.new("RGB", (50, 50)).save(shot_path, format="PNG")
    Image.new("RGB", (50, 50)).save(photo_path, format="JPEG")

    # Simulate a database created before the is_screenshot column existed.
    old_schema_conn = sqlite3.connect(db_path)
    old_schema_conn.execute(
        "CREATE TABLE files (path TEXT PRIMARY KEY, size INTEGER NOT NULL, "
        "mtime REAL NOT NULL, phash TEXT NOT NULL, width INTEGER NOT NULL, "
        "height INTEGER NOT NULL, file_size INTEGER NOT NULL, "
        "date_taken TEXT NOT NULL, sharpness REAL NOT NULL)"
    )
    for path in (shot_path, photo_path):
        old_schema_conn.execute(
            "INSERT INTO files (path, size, mtime, phash, width, height, "
            "file_size, date_taken, sharpness) VALUES (?, 1, 1.0, 'h', 50, 50, 1, "
            "'2024-01-01', 0.0)",
            (str(path),),
        )
    old_schema_conn.commit()
    old_schema_conn.close()

    conn = db.init_db(db_path)

    by_path = {f["path"]: f for f in db.all_files(conn)}
    assert by_path[str(shot_path)]["is_screenshot"] is True
    assert by_path[str(photo_path)]["is_screenshot"] is False


def test_resolved_groups_round_trip(tmp_path):
    conn = db.init_db(tmp_path / "test.db")
    assert db.is_group_resolved(conn, "g1") is False

    db.mark_group_resolved(conn, "g1")

    assert db.is_group_resolved(conn, "g1") is True
    assert db.is_group_resolved(conn, "g2") is False
