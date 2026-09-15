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
    db.upsert_file(conn, "/a.jpg", 100, 1.0, "abc123", 800, 600, 100, "2024-01-01", 12.5)

    entry = db.get_cached_entry(conn, "/a.jpg", 100, 1.0)

    assert entry["phash"] == "abc123"
    assert entry["width"] == 800
    assert entry["height"] == 600
    assert entry["file_size"] == 100
    assert entry["date_taken"] == "2024-01-01"
    assert entry["sharpness"] == 12.5


def test_get_cached_entry_misses_on_mtime_change(tmp_path):
    conn = db.init_db(tmp_path / "test.db")
    db.upsert_file(conn, "/a.jpg", 100, 1.0, "abc123", 800, 600, 100, "2024-01-01", 12.5)

    assert db.get_cached_entry(conn, "/a.jpg", 100, 2.0) is None


def test_upsert_overwrites_existing_row(tmp_path):
    conn = db.init_db(tmp_path / "test.db")
    db.upsert_file(conn, "/a.jpg", 100, 1.0, "old", 800, 600, 100, "2024-01-01", 12.5)
    db.upsert_file(conn, "/a.jpg", 200, 2.0, "new", 400, 300, 200, "2024-02-01", 5.0)

    assert db.get_cached_entry(conn, "/a.jpg", 100, 1.0) is None
    entry = db.get_cached_entry(conn, "/a.jpg", 200, 2.0)
    assert entry["phash"] == "new"


def test_log_scan_error(tmp_path):
    conn = db.init_db(tmp_path / "test.db")
    db.log_scan_error(conn, "/bad.jpg", "cannot identify image file")

    row = conn.execute(
        "SELECT error FROM scan_errors WHERE path = ?", ("/bad.jpg",)
    ).fetchone()
    assert row[0] == "cannot identify image file"


def test_all_files_returns_every_row(tmp_path):
    conn = db.init_db(tmp_path / "test.db")
    db.upsert_file(conn, "/a.jpg", 100, 1.0, "hash-a", 800, 600, 100, "2024-01-01", 12.5)
    db.upsert_file(conn, "/b.jpg", 200, 2.0, "hash-b", 400, 300, 200, "2024-02-01", 5.0)

    files = db.all_files(conn)

    assert {f["path"] for f in files} == {"/a.jpg", "/b.jpg"}


def test_delete_file_removes_row(tmp_path):
    conn = db.init_db(tmp_path / "test.db")
    db.upsert_file(conn, "/a.jpg", 100, 1.0, "hash-a", 800, 600, 100, "2024-01-01", 12.5)
    db.upsert_file(conn, "/b.jpg", 200, 2.0, "hash-b", 400, 300, 200, "2024-02-01", 5.0)

    db.delete_file(conn, "/a.jpg")

    paths = {f["path"] for f in db.all_files(conn)}
    assert paths == {"/b.jpg"}


def test_resolved_groups_round_trip(tmp_path):
    conn = db.init_db(tmp_path / "test.db")
    assert db.is_group_resolved(conn, "g1") is False

    db.mark_group_resolved(conn, "g1")

    assert db.is_group_resolved(conn, "g1") is True
    assert db.is_group_resolved(conn, "g2") is False
