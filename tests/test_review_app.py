import json
import re
import shutil
from io import BytesIO
from pathlib import Path

from PIL import Image

import db
import hashing
import review_app


def _make_image(path, color, size):
    Image.new("RGB", size, color=color).save(path)


def _seed_photos(tmp_path, specs):
    """specs: list of (filename, color, size). Returns (conn, [Path, ...])."""
    conn = db.init_db(tmp_path / "photo_deduper.db")
    paths = []
    for filename, color, size in specs:
        path = tmp_path / filename
        _make_image(path, color, size)
        stat = path.stat()
        phash = str(hashing.compute_phash(path))
        sharpness = hashing.compute_sharpness(path)
        with Image.open(path) as img:
            width, height = img.size
        db.upsert_file(
            conn, str(path), stat.st_size, stat.st_mtime, phash,
            width, height, stat.st_size, "2024-01-01", sharpness,
        )
        paths.append(path)
    return conn, paths


def _seed_duplicate_group(tmp_path):
    conn, _ = _seed_photos(
        tmp_path,
        [
            ("a.jpg", (200, 50, 50), (400, 300)),
            ("b.jpg", (200, 50, 50), (200, 150)),
        ],
    )
    return conn


def _seed_duplicate_group_of_three(tmp_path):
    conn, paths = _seed_photos(
        tmp_path,
        [
            ("a.jpg", (200, 50, 50), (400, 300)),
            ("b.jpg", (200, 50, 50), (200, 150)),
            ("c.jpg", (200, 50, 50), (100, 75)),
        ],
    )
    return conn, paths


def test_index_lists_unresolved_groups(tmp_path):
    _seed_duplicate_group(tmp_path)
    client = review_app.create_app(str(tmp_path)).test_client()

    response = client.get("/")

    assert response.status_code == 200
    assert b"Resolve group" in response.data


def test_index_shows_no_groups_message_when_empty(tmp_path):
    client = review_app.create_app(str(tmp_path)).test_client()

    response = client.get("/")

    assert b"No duplicate groups left to review" in response.data


def test_thumbnail_applies_exif_orientation(tmp_path):
    path = tmp_path / "sideways.jpg"
    img = Image.new("RGB", (40, 20), color=(255, 0, 0))
    exif = img.getexif()
    exif[0x0112] = 6  # rotated 90 degrees; correct display is portrait
    img.save(path, exif=exif)
    client = review_app.create_app(str(tmp_path)).test_client()

    response = client.get("/thumbnail", query_string={"path": str(path)})

    assert response.status_code == 200
    with Image.open(BytesIO(response.data)) as thumb:
        width, height = thumb.size
    assert height > width


def test_thumbnail_rejects_path_outside_root(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    outside = tmp_path / "outside.jpg"
    _make_image(outside, (0, 0, 255), size=(50, 50))
    client = review_app.create_app(str(root)).test_client()

    response = client.get("/thumbnail", query_string={"path": str(outside)})

    assert response.status_code == 403


def test_resolve_moves_unkept_files_and_hides_group(tmp_path):
    conn = _seed_duplicate_group(tmp_path)
    client = review_app.create_app(str(tmp_path)).test_client()

    html = client.get("/").data.decode()
    group_id = re.search(r'name="group_id" value="([^"]+)"', html).group(1)
    all_paths = re.findall(r'name="all_paths" value="([^"]+)"', html)
    keep_path = all_paths[0]

    response = client.post(
        "/resolve",
        data={"group_id": group_id, "keep": keep_path, "all_paths": all_paths},
        follow_redirects=True,
    )

    assert response.status_code == 200
    quarantine_dir = tmp_path / "_duplicates_review"
    moved_files = list(quarantine_dir.iterdir())
    assert len(moved_files) == len(all_paths) - 1
    assert b"No duplicate groups left to review" in response.data

    # The stale cache row for the quarantined (non-kept) file should be gone.
    remaining_paths = {f["path"] for f in db.all_files(conn)}
    assert keep_path in remaining_paths
    for path_str in all_paths:
        if path_str != keep_path:
            assert path_str not in remaining_paths

    # moves.log should have one well-formed JSON line per moved file.
    moves_log = tmp_path / "moves.log"
    lines = moves_log.read_text().strip().splitlines()
    assert len(lines) == len(moved_files)
    for line in lines:
        entry = json.loads(line)
        assert set(entry.keys()) == {"original_path", "new_path", "timestamp"}


def test_resolve_remove_all_moves_every_photo_without_a_keeper(tmp_path):
    conn = _seed_duplicate_group(tmp_path)
    client = review_app.create_app(str(tmp_path)).test_client()

    html = client.get("/").data.decode()
    group_id = re.search(r'name="group_id" value="([^"]+)"', html).group(1)
    all_paths = re.findall(r'name="all_paths" value="([^"]+)"', html)

    response = client.post(
        "/resolve",
        data={"group_id": group_id, "action": "remove_all", "all_paths": all_paths},
        follow_redirects=True,
    )

    assert response.status_code == 200
    quarantine_dir = tmp_path / "_duplicates_review"
    moved_files = list(quarantine_dir.iterdir())
    assert len(moved_files) == len(all_paths)
    assert b"No duplicate groups left to review" in response.data
    assert db.all_files(conn) == []


def test_resolve_reports_move_failure_and_keeps_group_unresolved(tmp_path, monkeypatch):
    _seed_duplicate_group(tmp_path)
    client = review_app.create_app(str(tmp_path)).test_client()

    html = client.get("/").data.decode()
    group_id = re.search(r'name="group_id" value="([^"]+)"', html).group(1)
    all_paths = re.findall(r'name="all_paths" value="([^"]+)"', html)
    keep_path = all_paths[0]
    blocked_path = all_paths[1]

    # Simulate a genuine move failure (e.g. a permissions problem or a full
    # disk) for one specific file, while letting real moves happen normally.
    real_move = shutil.move

    def flaky_move(src, dst, *args, **kwargs):
        if src == blocked_path:
            raise OSError(f"simulated failure moving {src}")
        return real_move(src, dst, *args, **kwargs)

    monkeypatch.setattr(review_app.shutil, "move", flaky_move)

    response = client.post(
        "/resolve",
        data={"group_id": group_id, "keep": keep_path, "all_paths": all_paths},
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert b"Failed to move" in response.data
    assert Path(blocked_path).exists()  # move failed, source untouched

    html_after = client.get("/").data.decode()
    assert "Resolve group" in html_after  # group still unresolved


def test_resolve_partial_failure_recovers_on_retry(tmp_path, monkeypatch):
    conn, paths = _seed_duplicate_group_of_three(tmp_path)
    client = review_app.create_app(str(tmp_path)).test_client()

    html = client.get("/").data.decode()
    group_id = re.search(r'name="group_id" value="([^"]+)"', html).group(1)
    all_paths = re.findall(r'name="all_paths" value="([^"]+)"', html)
    keep_path = all_paths[0]
    blocked_path = all_paths[2]

    # Fail every move of `blocked_path` until `failing["active"]` is turned
    # off, modeling a transient problem (e.g. a full disk) that the user
    # fixes before retrying, while other files move normally throughout.
    real_move = shutil.move
    failing = {"active": True}

    def flaky_move(src, dst, *args, **kwargs):
        if src == blocked_path and failing["active"]:
            raise shutil.Error(f"simulated failure moving {src}")
        return real_move(src, dst, *args, **kwargs)

    monkeypatch.setattr(review_app.shutil, "move", flaky_move)

    form = {"group_id": group_id, "keep": keep_path, "all_paths": all_paths}

    first = client.post("/resolve", data=form, follow_redirects=True)
    assert b"Failed to move" in first.data
    assert "Resolve group" in first.data.decode()  # still unresolved

    # The unblocked file was moved and its stale row pruned; the blocked
    # file's source is still on disk and its row still present.
    remaining_after_first = {f["path"] for f in db.all_files(conn)}
    for path_str in all_paths:
        if path_str in (keep_path, blocked_path):
            assert path_str in remaining_after_first
        else:
            assert path_str not in remaining_after_first
            assert not Path(path_str).exists()

    # Now the underlying problem is fixed; retry the exact same POST. The
    # already-moved file must NOT be re-attempted (its source is gone), and
    # the previously-blocked file should now move successfully.
    failing["active"] = False

    def watch_move(src, dst, *args, **kwargs):
        assert Path(src).exists(), f"attempted to re-move already-gone file {src}"
        return real_move(src, dst, *args, **kwargs)

    monkeypatch.setattr(review_app.shutil, "move", watch_move)

    second = client.post("/resolve", data=form, follow_redirects=True)

    assert second.status_code == 200
    assert b"Failed to move" not in second.data
    assert b"No duplicate groups left to review" in second.data

    remaining_after_second = {f["path"] for f in db.all_files(conn)}
    assert remaining_after_second == {keep_path}
    assert Path(blocked_path).exists() is False
    assert Path(keep_path).exists()

    quarantine_dir = tmp_path / "_duplicates_review"
    quarantine_contents = list(quarantine_dir.iterdir())
    assert len(quarantine_contents) == 2  # the two non-kept files


def test_resolve_with_no_keepers_touches_nothing(tmp_path):
    _seed_duplicate_group(tmp_path)
    client = review_app.create_app(str(tmp_path)).test_client()

    html = client.get("/").data.decode()
    group_id = re.search(r'name="group_id" value="([^"]+)"', html).group(1)
    all_paths = re.findall(r'name="all_paths" value="([^"]+)"', html)

    response = client.post(
        "/resolve",
        data={"group_id": group_id, "all_paths": all_paths},
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert b"Select at least one photo to keep" in response.data
    for path_str in all_paths:
        assert Path(path_str).exists()
    assert not (tmp_path / "_duplicates_review").exists()

    html_after = client.get("/").data.decode()
    assert "Resolve group" in html_after  # group still listed


def test_resolve_rejects_path_outside_root(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    conn, paths = _seed_photos(
        root,
        [
            ("a.jpg", (200, 50, 50), (400, 300)),
            ("b.jpg", (200, 50, 50), (200, 150)),
        ],
    )
    outside = tmp_path / "outside.jpg"
    _make_image(outside, (0, 0, 255), size=(50, 50))

    client = review_app.create_app(str(root)).test_client()
    html = client.get("/").data.decode()
    group_id = re.search(r'name="group_id" value="([^"]+)"', html).group(1)
    all_paths = re.findall(r'name="all_paths" value="([^"]+)"', html)
    keep_path = all_paths[0]

    response = client.post(
        "/resolve",
        data={
            "group_id": group_id,
            "keep": keep_path,
            "all_paths": all_paths + [str(outside)],
        },
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert b"outside the scanned directory" in response.data
    assert outside.exists()
    for path_str in all_paths:
        assert Path(path_str).exists()
    assert not (root / "_duplicates_review").exists()


def test_importing_review_app_registers_heic_support():
    from PIL import Image

    assert ".heic" in Image.registered_extensions()
