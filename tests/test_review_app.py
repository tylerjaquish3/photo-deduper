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
    """specs: list of (filename, color, size[, is_screenshot]). Returns (conn, [Path, ...])."""
    conn = db.init_db(tmp_path / "photo_deduper.db")
    paths = []
    for spec in specs:
        filename, color, size = spec[:3]
        is_screenshot = spec[3] if len(spec) > 3 else False
        path = tmp_path / filename
        _make_image(path, color, size)
        stat = path.stat()
        phash = str(hashing.compute_phash(path))
        sharpness = hashing.compute_sharpness(path)
        with Image.open(path) as img:
            width, height = img.size
        db.upsert_file(
            conn, str(path), stat.st_size, stat.st_mtime, phash,
            width, height, stat.st_size, "2024-01-01", sharpness, is_screenshot,
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


def _seed_two_duplicate_groups(tmp_path):
    """Two independent duplicate groups with hand-picked phashes so they
    cluster separately regardless of actual pixel content."""
    conn = db.init_db(tmp_path / "photo_deduper.db")
    specs = [
        ("group1_a.jpg", "0000000000000000"),
        ("group1_b.jpg", "0000000000000001"),
        ("group2_a.jpg", "ffffffffffffffff"),
        ("group2_b.jpg", "fffffffffffffffe"),
    ]
    paths = []
    for filename, phash in specs:
        path = tmp_path / filename
        _make_image(path, (120, 120, 120), (200, 150))
        stat = path.stat()
        with Image.open(path) as img:
            width, height = img.size
        db.upsert_file(
            conn, str(path), stat.st_size, stat.st_mtime, phash,
            width, height, stat.st_size, "2024-01-01", 10.0, False,
        )
        paths.append(path)
    return conn, paths


def _seed_three_duplicate_groups(tmp_path):
    """Three independent duplicate groups (2 photos each), each pair close
    together but far from every other group's pair."""
    conn = db.init_db(tmp_path / "photo_deduper.db")
    specs = [
        ("group1_a.jpg", "0000000000000000"),
        ("group1_b.jpg", "0000000000000001"),
        ("group2_a.jpg", "ffffffffffffffff"),
        ("group2_b.jpg", "fffffffffffffffe"),
        ("group3_a.jpg", "0f0f0f0f0f0f0f0f"),
        ("group3_b.jpg", "0f0f0f0f0f0f0f0e"),
    ]
    paths = []
    for filename, phash in specs:
        path = tmp_path / filename
        _make_image(path, (120, 120, 120), (200, 150))
        stat = path.stat()
        with Image.open(path) as img:
            width, height = img.size
        db.upsert_file(
            conn, str(path), stat.st_size, stat.st_mtime, phash,
            width, height, stat.st_size, "2024-01-01", 10.0, False,
        )
        paths.append(path)
    return conn, paths


def _extract_groups(html):
    """Returns [(group_id, [all_paths...]), ...] in page order."""
    group_ids = re.findall(r'name="group_ids" value="([^"]+)"', html)
    groups = []
    for gid in group_ids:
        all_paths = re.findall(rf'name="all_paths_{gid}" value="([^"]+)"', html)
        groups.append((gid, all_paths))
    return groups


def test_index_lists_unresolved_groups(tmp_path):
    _seed_duplicate_group(tmp_path)
    client = review_app.create_app(str(tmp_path)).test_client()

    response = client.get("/")

    assert response.status_code == 200
    assert b"Resolve" in response.data


def test_index_shows_no_groups_message_when_empty(tmp_path):
    client = review_app.create_app(str(tmp_path)).test_client()

    response = client.get("/")

    assert b"No duplicate groups left to review" in response.data


def test_limit_groups_stops_before_exceeding_max():
    groups = [{"photos": [1, 2]}, {"photos": [3, 4]}, {"photos": [5, 6]}]

    visible, hidden = review_app._limit_groups(groups, max_photos=4)

    assert visible == groups[:2]
    assert hidden == 1


def test_limit_groups_always_includes_first_group_even_if_over_limit():
    groups = [{"photos": [1, 2, 3, 4, 5]}, {"photos": [6, 7]}]

    visible, hidden = review_app._limit_groups(groups, max_photos=2)

    assert visible == groups[:1]
    assert hidden == 1


def test_limit_groups_returns_all_when_under_limit():
    groups = [{"photos": [1, 2]}, {"photos": [3, 4]}]

    visible, hidden = review_app._limit_groups(groups, max_photos=100)

    assert visible == groups
    assert hidden == 0


def test_index_limits_groups_and_shows_load_more_message(tmp_path, monkeypatch):
    monkeypatch.setattr(review_app, "MAX_PHOTOS_PER_PAGE", 4)
    _seed_three_duplicate_groups(tmp_path)
    client = review_app.create_app(str(tmp_path)).test_client()

    response = client.get("/")
    html = response.data.decode()

    assert response.status_code == 200
    assert "Duplicate groups (3 remaining)" in html
    assert "Showing 2 of 3 groups" in html
    assert "1 more group not shown yet" in html
    assert len(_extract_groups(html)) == 2


def test_index_shows_no_load_more_message_when_everything_fits(tmp_path):
    _seed_three_duplicate_groups(tmp_path)
    client = review_app.create_app(str(tmp_path)).test_client()

    html = client.get("/").data.decode()

    assert "not shown yet" not in html
    assert len(_extract_groups(html)) == 3


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


def test_resolve_batch_moves_unkept_files_and_hides_group(tmp_path):
    conn = _seed_duplicate_group(tmp_path)
    client = review_app.create_app(str(tmp_path)).test_client()

    html = client.get("/").data.decode()
    (gid, all_paths), = _extract_groups(html)
    keep_path = all_paths[0]

    response = client.post(
        "/resolve_batch",
        data={
            "group_ids": gid,
            f"action_{gid}": "resolve",
            f"keep_{gid}": keep_path,
            f"all_paths_{gid}": all_paths,
        },
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


def test_resolve_batch_remove_all_moves_every_photo_without_a_keeper(tmp_path):
    conn = _seed_duplicate_group(tmp_path)
    client = review_app.create_app(str(tmp_path)).test_client()

    html = client.get("/").data.decode()
    (gid, all_paths), = _extract_groups(html)

    response = client.post(
        "/resolve_batch",
        data={
            "group_ids": gid,
            f"action_{gid}": "remove_all",
            f"all_paths_{gid}": all_paths,
        },
        follow_redirects=True,
    )

    assert response.status_code == 200
    quarantine_dir = tmp_path / "_duplicates_review"
    moved_files = list(quarantine_dir.iterdir())
    assert len(moved_files) == len(all_paths)
    assert b"No duplicate groups left to review" in response.data
    assert db.all_files(conn) == []


def test_resolve_batch_keep_all_moves_nothing_and_resolves_group(tmp_path):
    conn = _seed_duplicate_group(tmp_path)
    client = review_app.create_app(str(tmp_path)).test_client()

    html = client.get("/").data.decode()
    (gid, all_paths), = _extract_groups(html)

    response = client.post(
        "/resolve_batch",
        data={
            "group_ids": gid,
            f"action_{gid}": "keep_all",
            f"all_paths_{gid}": all_paths,
        },
        follow_redirects=True,
    )

    assert response.status_code == 200
    quarantine_dir = tmp_path / "_duplicates_review"
    assert list(quarantine_dir.glob("*")) == []
    assert b"No duplicate groups left to review" in response.data
    remaining_paths = {f["path"] for f in db.all_files(conn)}
    assert remaining_paths == set(all_paths)


def test_resolve_batch_handles_multiple_groups_in_one_save(tmp_path):
    conn, _ = _seed_two_duplicate_groups(tmp_path)
    client = review_app.create_app(str(tmp_path)).test_client()

    html = client.get("/").data.decode()
    groups = _extract_groups(html)
    assert len(groups) == 2

    (gid_a, paths_a), (gid_b, paths_b) = groups
    keep_a = paths_a[0]

    response = client.post(
        "/resolve_batch",
        data={
            "group_ids": [gid_a, gid_b],
            f"action_{gid_a}": "resolve",
            f"keep_{gid_a}": keep_a,
            f"all_paths_{gid_a}": paths_a,
            f"action_{gid_b}": "remove_all",
            f"all_paths_{gid_b}": paths_b,
        },
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert b"No duplicate groups left to review" in response.data

    remaining_paths = {f["path"] for f in db.all_files(conn)}
    assert remaining_paths == {keep_a}

    quarantine_dir = tmp_path / "_duplicates_review"
    # the non-kept file from group A, plus both files from group B
    assert len(list(quarantine_dir.iterdir())) == 3


def test_resolve_batch_skips_groups_without_a_queued_action(tmp_path):
    _seed_duplicate_group(tmp_path)
    client = review_app.create_app(str(tmp_path)).test_client()

    html = client.get("/").data.decode()
    (gid, all_paths), = _extract_groups(html)

    # Simulate a Save with this group never marked (no action_<gid> field at
    # all) — it must be left untouched for next time, not treated as an error.
    response = client.post(
        "/resolve_batch", data={"group_ids": gid}, follow_redirects=True
    )

    assert response.status_code == 200
    for path_str in all_paths:
        assert Path(path_str).exists()
    assert not (tmp_path / "_duplicates_review").exists()

    html_after = client.get("/").data.decode()
    assert "Resolve" in html_after  # still listed, untouched


def test_resolve_batch_reports_move_failure_and_keeps_group_unresolved(tmp_path, monkeypatch):
    _seed_duplicate_group(tmp_path)
    client = review_app.create_app(str(tmp_path)).test_client()

    html = client.get("/").data.decode()
    (gid, all_paths), = _extract_groups(html)
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
        "/resolve_batch",
        data={
            "group_ids": gid,
            f"action_{gid}": "resolve",
            f"keep_{gid}": keep_path,
            f"all_paths_{gid}": all_paths,
        },
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert b"Failed to move" in response.data
    assert Path(blocked_path).exists()  # move failed, source untouched

    html_after = client.get("/").data.decode()
    assert "Resolve" in html_after  # group still unresolved


def test_resolve_batch_partial_failure_recovers_on_retry(tmp_path, monkeypatch):
    conn, paths = _seed_duplicate_group_of_three(tmp_path)
    client = review_app.create_app(str(tmp_path)).test_client()

    html = client.get("/").data.decode()
    (gid, all_paths), = _extract_groups(html)
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

    form = {
        "group_ids": gid,
        f"action_{gid}": "resolve",
        f"keep_{gid}": keep_path,
        f"all_paths_{gid}": all_paths,
    }

    first = client.post("/resolve_batch", data=form, follow_redirects=True)
    assert b"Failed to move" in first.data
    assert "Resolve" in first.data.decode()  # still unresolved

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

    second = client.post("/resolve_batch", data=form, follow_redirects=True)

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


def test_resolve_batch_with_no_keepers_touches_nothing(tmp_path):
    _seed_duplicate_group(tmp_path)
    client = review_app.create_app(str(tmp_path)).test_client()

    html = client.get("/").data.decode()
    (gid, all_paths), = _extract_groups(html)

    response = client.post(
        "/resolve_batch",
        data={
            "group_ids": gid,
            f"action_{gid}": "resolve",
            f"all_paths_{gid}": all_paths,
        },
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert b"Select at least one photo to keep" in response.data
    for path_str in all_paths:
        assert Path(path_str).exists()
    assert not (tmp_path / "_duplicates_review").exists()

    html_after = client.get("/").data.decode()
    assert "Resolve" in html_after  # group still listed


def test_resolve_batch_rejects_path_outside_root(tmp_path):
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
    (gid, all_paths), = _extract_groups(html)
    keep_path = all_paths[0]

    response = client.post(
        "/resolve_batch",
        data={
            "group_ids": gid,
            f"action_{gid}": "resolve",
            f"keep_{gid}": keep_path,
            f"all_paths_{gid}": all_paths + [str(outside)],
        },
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert b"outside the scanned directory" in response.data
    assert outside.exists()
    for path_str in all_paths:
        assert Path(path_str).exists()
    assert not (root / "_duplicates_review").exists()


def test_screenshots_lists_only_files_flagged_as_screenshots(tmp_path):
    conn, paths = _seed_photos(
        tmp_path,
        [
            ("shot_a.png", (10, 20, 30), (100, 200), True),
            ("shot_b.png", (10, 20, 30), (100, 200), True),
            ("vacation.jpg", (200, 50, 50), (400, 300), False),
        ],
    )
    client = review_app.create_app(str(tmp_path)).test_client()

    response = client.get("/screenshots")

    assert response.status_code == 200
    html = response.data.decode()
    assert "Screenshots (2)" in html
    assert str(paths[0]) in html
    assert str(paths[1]) in html
    assert str(paths[2]) not in html


def test_screenshots_shows_empty_message_when_none_found(tmp_path):
    _seed_photos(tmp_path, [("vacation.jpg", (200, 50, 50), (400, 300))])
    client = review_app.create_app(str(tmp_path)).test_client()

    response = client.get("/screenshots")

    assert b"No screenshots found" in response.data


def test_delete_screenshots_batch_removes_only_checked_ones(tmp_path):
    conn, paths = _seed_photos(
        tmp_path,
        [
            ("shot_a.png", (10, 20, 30), (100, 200), True),
            ("shot_b.png", (10, 20, 30), (100, 200), True),
            ("shot_c.png", (10, 20, 30), (100, 200), True),
        ],
    )
    to_delete = [paths[0], paths[2]]
    kept = paths[1]
    client = review_app.create_app(str(tmp_path)).test_client()

    response = client.post(
        "/screenshots/delete_batch",
        data={"path": [str(p) for p in to_delete]},
        follow_redirects=True,
    )

    assert response.status_code == 200
    for path in to_delete:
        assert not path.exists()
    assert kept.exists()

    quarantine_dir = tmp_path / "_duplicates_review"
    assert len(list(quarantine_dir.iterdir())) == 2

    remaining_paths = {f["path"] for f in db.all_files(conn)}
    assert remaining_paths == {str(kept)}

    html_after = response.data.decode()
    assert "Screenshots (1)" in html_after


def test_delete_screenshots_batch_rejects_path_outside_root(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    outside = tmp_path / "outside.png"
    _make_image(outside, (0, 0, 255), size=(50, 50))
    client = review_app.create_app(str(root)).test_client()

    response = client.post(
        "/screenshots/delete_batch",
        data={"path": str(outside)},
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert b"outside the scanned directory" in response.data
    assert outside.exists()


def test_importing_review_app_registers_heic_support():
    from PIL import Image

    assert ".heic" in Image.registered_extensions()
