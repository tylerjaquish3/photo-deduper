import re
from pathlib import Path

from PIL import Image

import db
import hashing
import review_app


def _make_image(path, color, size):
    Image.new("RGB", size, color=color).save(path)


def _seed_duplicate_group(tmp_path):
    img_a = tmp_path / "a.jpg"
    img_b = tmp_path / "b.jpg"
    _make_image(img_a, (200, 50, 50), size=(400, 300))
    _make_image(img_b, (200, 50, 50), size=(200, 150))

    conn = db.init_db(tmp_path / "photo_deduper.db")
    for path in (img_a, img_b):
        stat = path.stat()
        phash = str(hashing.compute_phash(path))
        sharpness = hashing.compute_sharpness(path)
        with Image.open(path) as img:
            width, height = img.size
        db.upsert_file(
            conn, str(path), stat.st_size, stat.st_mtime, phash,
            width, height, stat.st_size, "2024-01-01", sharpness,
        )
    return conn


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


def test_thumbnail_rejects_path_outside_root(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    outside = tmp_path / "outside.jpg"
    _make_image(outside, (0, 0, 255), size=(50, 50))
    client = review_app.create_app(str(root)).test_client()

    response = client.get("/thumbnail", query_string={"path": str(outside)})

    assert response.status_code == 403


def test_resolve_moves_unkept_files_and_hides_group(tmp_path):
    _seed_duplicate_group(tmp_path)
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
    assert len(list(quarantine_dir.iterdir())) == len(all_paths) - 1
    assert b"No duplicate groups left to review" in response.data


def test_resolve_reports_move_failure_and_keeps_group_unresolved(tmp_path):
    _seed_duplicate_group(tmp_path)
    client = review_app.create_app(str(tmp_path)).test_client()

    html = client.get("/").data.decode()
    group_id = re.search(r'name="group_id" value="([^"]+)"', html).group(1)
    all_paths = re.findall(r'name="all_paths" value="([^"]+)"', html)
    keep_path = all_paths[0]
    missing_path = all_paths[1]

    Path(missing_path).unlink()  # so shutil.move on it fails

    response = client.post(
        "/resolve",
        data={"group_id": group_id, "keep": keep_path, "all_paths": all_paths},
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert b"Failed to move" in response.data

    html_after = client.get("/").data.decode()
    assert "Resolve group" in html_after  # group still unresolved
