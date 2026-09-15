import os

from PIL import Image

import db
import scan


def _make_image(path, color=(255, 0, 0), size=(50, 50)):
    Image.new("RGB", size, color=color).save(path)


def test_scan_directory_populates_cache(tmp_path):
    _make_image(tmp_path / "one.jpg")
    _make_image(tmp_path / "two.jpg", color=(0, 255, 0))
    conn = db.init_db(tmp_path / "photo_deduper.db")

    summary = scan.scan_directory(str(tmp_path), conn)

    assert summary == {"scanned": 2, "skipped": 0, "errors": 0}
    assert len(db.all_files(conn)) == 2


def test_scan_directory_skips_already_cached_files_on_rescan(tmp_path):
    _make_image(tmp_path / "one.jpg")
    conn = db.init_db(tmp_path / "photo_deduper.db")
    scan.scan_directory(str(tmp_path), conn)

    summary = scan.scan_directory(str(tmp_path), conn)

    assert summary == {"scanned": 0, "skipped": 1, "errors": 0}


def test_scan_directory_logs_errors_and_continues(tmp_path):
    (tmp_path / "corrupt.jpg").write_bytes(b"not a real image")
    _make_image(tmp_path / "good.jpg")
    conn = db.init_db(tmp_path / "photo_deduper.db")

    summary = scan.scan_directory(str(tmp_path), conn)

    assert summary == {"scanned": 1, "skipped": 0, "errors": 1}
    errors = conn.execute("SELECT path FROM scan_errors").fetchall()
    assert len(errors) == 1


def test_scan_directory_skips_quarantine_folder(tmp_path):
    quarantine = tmp_path / "_duplicates_review"
    quarantine.mkdir()
    _make_image(quarantine / "already_moved.jpg")
    _make_image(tmp_path / "good.jpg")
    conn = db.init_db(tmp_path / "photo_deduper.db")

    summary = scan.scan_directory(str(tmp_path), conn)

    assert summary == {"scanned": 1, "skipped": 0, "errors": 0}


def test_find_image_files_only_matches_supported_extensions(tmp_path):
    _make_image(tmp_path / "photo.jpg")
    (tmp_path / "notes.txt").write_text("hello")

    found = list(scan.find_image_files(str(tmp_path)))

    assert len(found) == 1
    assert found[0].endswith("photo.jpg")


def test_scan_directory_stores_absolute_paths_when_given_relative_root(tmp_path, monkeypatch):
    subdir = tmp_path / "photos"
    subdir.mkdir()
    _make_image(subdir / "one.jpg")
    conn = db.init_db(tmp_path / "photo_deduper.db")

    monkeypatch.chdir(tmp_path)
    summary = scan.scan_directory("photos", conn)

    assert summary == {"scanned": 1, "skipped": 0, "errors": 0}
    stored_paths = [f["path"] for f in db.all_files(conn)]
    assert len(stored_paths) == 1
    assert os.path.isabs(stored_paths[0])
    assert stored_paths[0] == str(subdir / "one.jpg")


def test_scan_directory_handles_broken_symlink_without_aborting(tmp_path):
    _make_image(tmp_path / "good.jpg")
    broken_link = tmp_path / "broken.jpg"
    broken_link.symlink_to(tmp_path / "does_not_exist.jpg")
    conn = db.init_db(tmp_path / "photo_deduper.db")

    summary = scan.scan_directory(str(tmp_path), conn)

    assert summary == {"scanned": 1, "skipped": 0, "errors": 1}
    errors = conn.execute("SELECT path FROM scan_errors").fetchall()
    assert len(errors) == 1
