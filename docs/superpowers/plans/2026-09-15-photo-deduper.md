# Photo Deduper Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a local tool that scans a directory of photos, finds near-duplicate photos via perceptual hashing, and lets the user review each duplicate group in a browser UI to pick which copy to keep.

**Architecture:** Two Python CLI entry points (`scan.py`, `review.py`) sharing a SQLite cache (`photo_deduper.db`, stored at the root of the scanned directory). The scanner walks the directory and caches a perceptual hash + quality metadata per photo. The review app clusters cached hashes into duplicate groups with numpy-vectorized Hamming distance + union-find, serves a browser review page via Flask, and moves rejected photos into a `_duplicates_review/` quarantine folder on resolve.

**Tech Stack:** Python 3, Pillow, pillow-heif, ImageHash, numpy, opencv-python-headless, Flask, pytest.

**Spec:** `docs/superpowers/specs/2026-09-15-photo-deduper-design.md`

## Global Constraints

- Cache database filename: `photo_deduper.db`, stored at the root of the scanned directory.
- Quarantine folder name: `_duplicates_review`, created at the root of the scanned directory.
- Moves log filename: `moves.log`, JSON-lines format, stored at the root of the scanned directory.
- Supported image extensions: `.jpg`, `.jpeg`, `.png`, `.heic` (case-insensitive).
- Default Hamming-distance clustering threshold: 8 (out of 64 bits).
- Ranking order for suggested keeper: resolution (width × height) desc, then sharpness desc, then file size desc.
- Rejected photos are moved (never deleted) and every move is appended to `moves.log` before the group is marked resolved.
- New standalone project at `~/sites/photo-deduper`, own git repo, unrelated to the tylerwebco codebase.

---

### Task 1: Project scaffolding + SQLite cache module

**Files:**
- Create: `requirements.txt`
- Create: `.gitignore`
- Create: `db.py`
- Test: `tests/test_db.py`

**Interfaces:**
- Consumes: nothing (first task).
- Produces:
  - `db.init_db(db_path) -> sqlite3.Connection` — creates tables if absent, returns an open connection.
  - `db.get_cached_entry(conn, path: str, size: int, mtime: float) -> dict | None` — keys: `phash`, `width`, `height`, `file_size`, `date_taken`, `sharpness`.
  - `db.upsert_file(conn, path, size, mtime, phash, width, height, file_size, date_taken, sharpness) -> None`
  - `db.log_scan_error(conn, path: str, error) -> None`
  - `db.all_files(conn) -> list[dict]` — each dict has keys `path`, `phash`, `width`, `height`, `file_size`, `date_taken`, `sharpness`.
  - `db.is_group_resolved(conn, group_id: str) -> bool`
  - `db.mark_group_resolved(conn, group_id: str) -> None`

- [ ] **Step 1: Scaffold the project**

```bash
mkdir -p ~/sites/photo-deduper/tests
cd ~/sites/photo-deduper
python3 -m venv venv
source venv/bin/activate
```

Create `requirements.txt`:

```
Flask==3.0.3
Pillow==10.4.0
pillow-heif==0.18.0
ImageHash==4.3.1
numpy==2.0.1
opencv-python-headless==4.10.0.84
pytest==8.3.2
```

Create `.gitignore`:

```
venv/
__pycache__/
*.pyc
*.db
moves.log
_duplicates_review/
.pytest_cache/
```

Install:

```bash
pip install -r requirements.txt
```

- [ ] **Step 2: Write the failing tests**

Create `tests/test_db.py`:

```python
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


def test_resolved_groups_round_trip(tmp_path):
    conn = db.init_db(tmp_path / "test.db")
    assert db.is_group_resolved(conn, "g1") is False

    db.mark_group_resolved(conn, "g1")

    assert db.is_group_resolved(conn, "g1") is True
    assert db.is_group_resolved(conn, "g2") is False
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `pytest tests/test_db.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'db'`

- [ ] **Step 4: Implement `db.py`**

```python
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
    sharpness REAL NOT NULL
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
    conn.commit()
    return conn


def get_cached_entry(conn, path, size, mtime):
    row = conn.execute(
        "SELECT phash, width, height, file_size, date_taken, sharpness "
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
    }


def upsert_file(conn, path, size, mtime, phash, width, height, file_size, date_taken, sharpness):
    conn.execute(
        "INSERT INTO files "
        "(path, size, mtime, phash, width, height, file_size, date_taken, sharpness) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(path) DO UPDATE SET "
        "size=excluded.size, mtime=excluded.mtime, phash=excluded.phash, "
        "width=excluded.width, height=excluded.height, file_size=excluded.file_size, "
        "date_taken=excluded.date_taken, sharpness=excluded.sharpness",
        (path, size, mtime, phash, width, height, file_size, date_taken, sharpness),
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
        "SELECT path, phash, width, height, file_size, date_taken, sharpness FROM files"
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
        }
        for row in rows
    ]


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
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `pytest tests/test_db.py -v`
Expected: PASS (8 tests)

- [ ] **Step 6: Commit**

```bash
git add requirements.txt .gitignore db.py tests/test_db.py
git commit -m "feat: add SQLite cache module for scanned photo metadata"
```

---

### Task 2: Hashing & metadata module

**Files:**
- Create: `hashing.py`
- Test: `tests/test_hashing.py`

**Interfaces:**
- Consumes: nothing from Task 1.
- Produces:
  - `hashing.MissingHeicSupportError` — exception class.
  - `hashing.check_heic_support() -> None` — raises `MissingHeicSupportError` if `pillow-heif` isn't installed.
  - `hashing.compute_phash(path) -> imagehash.ImageHash` — call `str(...)` on the result to get the hex string stored in the DB.
  - `hashing.compute_sharpness(path) -> float`
  - `hashing.get_metadata(path, fallback_mtime: float) -> dict` — keys `width`, `height`, `date_taken` (ISO string).

- [ ] **Step 1: Write the failing tests**

Create `tests/test_hashing.py`:

```python
from datetime import datetime

import pytest
from PIL import Image, ImageDraw

import hashing


def _make_test_image(variant="stripes", size=(400, 300)):
    img = Image.new("RGB", size, color=(240, 240, 240))
    draw = ImageDraw.Draw(img)
    if variant == "stripes":
        for x in range(0, size[0], 10):
            color = (x % 256, (x * 2) % 256, (x * 3) % 256)
            draw.line([(x, 0), (x, size[1])], fill=color, width=10)
        draw.ellipse([50, 50, 150, 150], fill=(255, 0, 0))
    else:
        for y in range(0, size[1], 20):
            for x in range(0, size[0], 20):
                if (x // 20 + y // 20) % 2 == 0:
                    draw.rectangle([x, y, x + 20, y + 20], fill=(0, 0, 0))
    return img


@pytest.fixture
def sample_images(tmp_path):
    original = _make_test_image("stripes")
    original_path = tmp_path / "original.jpg"
    original.save(original_path, quality=95)

    resized_path = tmp_path / "resized.jpg"
    original.resize((200, 150)).save(resized_path, quality=95)

    recompressed_path = tmp_path / "recompressed.jpg"
    original.save(recompressed_path, quality=20)

    unrelated_path = tmp_path / "unrelated.jpg"
    _make_test_image("checkerboard").save(unrelated_path, quality=95)

    return {
        "original": original_path,
        "resized": resized_path,
        "recompressed": recompressed_path,
        "unrelated": unrelated_path,
    }


def test_resized_and_recompressed_copies_hash_close_to_original(sample_images):
    original_hash = hashing.compute_phash(sample_images["original"])
    resized_hash = hashing.compute_phash(sample_images["resized"])
    recompressed_hash = hashing.compute_phash(sample_images["recompressed"])
    unrelated_hash = hashing.compute_phash(sample_images["unrelated"])

    assert (original_hash - resized_hash) <= 8
    assert (original_hash - recompressed_hash) <= 8
    assert (original_hash - unrelated_hash) > 8


def test_compute_sharpness_returns_non_negative_float(sample_images):
    score = hashing.compute_sharpness(sample_images["original"])

    assert isinstance(score, float)
    assert score >= 0


def test_get_metadata_reads_dimensions(sample_images):
    metadata = hashing.get_metadata(sample_images["original"], fallback_mtime=1700000000)

    assert metadata["width"] == 400
    assert metadata["height"] == 300


def test_get_metadata_falls_back_to_mtime_when_no_exif_date(sample_images):
    metadata = hashing.get_metadata(sample_images["original"], fallback_mtime=1700000000)

    assert metadata["date_taken"]
    datetime.fromisoformat(metadata["date_taken"])  # must not raise


def test_check_heic_support_does_not_raise_when_installed():
    hashing.check_heic_support()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_hashing.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'hashing'`

- [ ] **Step 3: Implement `hashing.py`**

```python
from datetime import datetime

import cv2
import imagehash
import numpy as np
from PIL import ExifTags, Image

try:
    import pillow_heif

    pillow_heif.register_heif_opener()
except ImportError:
    pillow_heif = None


class MissingHeicSupportError(RuntimeError):
    pass


def check_heic_support():
    if pillow_heif is None:
        raise MissingHeicSupportError(
            "HEIC support is not installed. Run: pip install pillow-heif"
        )


def compute_phash(path):
    with Image.open(path) as img:
        return imagehash.phash(img)


def compute_sharpness(path):
    with Image.open(path) as img:
        gray = np.array(img.convert("L"))
    return float(cv2.Laplacian(gray, cv2.CV_64F).var())


_DATE_TAG_ID = next(k for k, v in ExifTags.TAGS.items() if v == "DateTimeOriginal")


def get_metadata(path, fallback_mtime):
    with Image.open(path) as img:
        width, height = img.size
        date_taken = None
        exif = img.getexif()
        if exif and _DATE_TAG_ID in exif:
            try:
                date_taken = datetime.strptime(
                    str(exif[_DATE_TAG_ID]), "%Y:%m:%d %H:%M:%S"
                ).isoformat()
            except ValueError:
                date_taken = None

    if date_taken is None:
        date_taken = datetime.fromtimestamp(fallback_mtime).isoformat()

    return {"width": width, "height": height, "date_taken": date_taken}
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/test_hashing.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add hashing.py tests/test_hashing.py
git commit -m "feat: add perceptual hashing and photo metadata extraction"
```

---

### Task 3: Grouping & ranking module

**Files:**
- Create: `grouping.py`
- Test: `tests/test_grouping.py`

**Interfaces:**
- Consumes: record dicts shaped like `db.all_files()` output (keys `path`, `phash`, `width`, `height`, `file_size`, `date_taken`, `sharpness`) from Task 1.
- Produces:
  - `grouping.hamming_distance(hex_a: str, hex_b: str) -> int`
  - `grouping.group_photos(records: list[dict], threshold: int = 8) -> list[list[dict]]` — each inner list is a duplicate group of 2+ records; singletons are dropped.
  - `grouping.rank_group(group: list[dict]) -> list[dict]` — sorted best-keeper-first.
  - `grouping.group_id(group: list[dict]) -> str` — stable regardless of input order.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_grouping.py`:

```python
import grouping


def _record(path, phash_hex, width=100, height=100, file_size=1000, sharpness=10.0):
    return {
        "path": path,
        "phash": phash_hex,
        "width": width,
        "height": height,
        "file_size": file_size,
        "sharpness": sharpness,
        "date_taken": "2024-01-01",
    }


def test_hamming_distance():
    assert grouping.hamming_distance("ff00000000000000", "ff00000000000000") == 0
    assert grouping.hamming_distance("ff00000000000000", "0000000000000000") == 8
    assert grouping.hamming_distance("ffffffffffffffff", "0000000000000000") == 64


def test_group_photos_clusters_similar_hashes_and_ignores_singletons():
    records = [
        _record("/a.jpg", "0000000000000000"),
        _record("/b.jpg", "0000000000000001"),  # 1 bit different from a
        _record("/c.jpg", "ffffffffffffffff"),  # far from both
    ]

    groups = grouping.group_photos(records, threshold=8)

    assert len(groups) == 1
    assert {r["path"] for r in groups[0]} == {"/a.jpg", "/b.jpg"}


def test_group_photos_returns_empty_when_nothing_clusters():
    records = [
        _record("/a.jpg", "0000000000000000"),
        _record("/b.jpg", "ffffffffffffffff"),
    ]

    assert grouping.group_photos(records, threshold=8) == []


def test_group_photos_handles_empty_input():
    assert grouping.group_photos([], threshold=8) == []


def test_group_photos_merges_transitively_connected_hashes():
    # a<->b within threshold, b<->c within threshold, a<->c is not directly
    # within threshold but all three must end up in one group.
    records = [
        _record("/a.jpg", "0000000000000000"),
        _record("/b.jpg", "00000000000000ff"),  # 8 bits from a
        _record("/c.jpg", "000000000000ffff"),  # 8 bits from b, 16 from a
    ]

    groups = grouping.group_photos(records, threshold=8)

    assert len(groups) == 1
    assert {r["path"] for r in groups[0]} == {"/a.jpg", "/b.jpg", "/c.jpg"}


def test_rank_group_prefers_resolution_then_sharpness_then_file_size():
    group = [
        _record("/small.jpg", "0", width=100, height=100, sharpness=50, file_size=500),
        _record("/big.jpg", "0", width=800, height=600, sharpness=10, file_size=200000),
        _record("/sharper_same_res.jpg", "0", width=100, height=100, sharpness=90, file_size=600),
    ]

    ranked = grouping.rank_group(group)

    assert [r["path"] for r in ranked] == [
        "/big.jpg",
        "/sharper_same_res.jpg",
        "/small.jpg",
    ]


def test_group_id_is_stable_regardless_of_order():
    group_a = [_record("/a.jpg", "0"), _record("/b.jpg", "0")]
    group_b = [_record("/b.jpg", "0"), _record("/a.jpg", "0")]

    assert grouping.group_id(group_a) == grouping.group_id(group_b)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_grouping.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'grouping'`

- [ ] **Step 3: Implement `grouping.py`**

```python
import hashlib

import numpy as np

_POPCOUNT_TABLE = np.array([bin(i).count("1") for i in range(256)], dtype=np.uint8)


def hamming_distance(hex_a, hex_b):
    return bin(int(hex_a, 16) ^ int(hex_b, 16)).count("1")


def _popcount_uint64(arr):
    byte_view = arr.view(np.uint8).reshape(arr.shape + (8,))
    return _POPCOUNT_TABLE[byte_view].sum(axis=-1)


class _UnionFind:
    def __init__(self, items):
        self.parent = {item: item for item in items}

    def find(self, item):
        while self.parent[item] != item:
            self.parent[item] = self.parent[self.parent[item]]
            item = self.parent[item]
        return item

    def union(self, a, b):
        root_a, root_b = self.find(a), self.find(b)
        if root_a != root_b:
            self.parent[root_a] = root_b


def group_photos(records, threshold=8, batch_size=500):
    if not records:
        return []

    paths = [r["path"] for r in records]
    hashes = np.array([int(r["phash"], 16) for r in records], dtype=np.uint64)
    n = len(hashes)

    uf = _UnionFind(paths)
    for start in range(0, n, batch_size):
        end = min(start + batch_size, n)
        batch = hashes[start:end]
        distances = _popcount_uint64(np.bitwise_xor(batch[:, None], hashes[None, :]))
        rows, cols = np.where(distances <= threshold)
        for row, col in zip(rows, cols):
            i, j = start + int(row), int(col)
            if i < j:
                uf.union(paths[i], paths[j])

    clusters = {}
    for record in records:
        root = uf.find(record["path"])
        clusters.setdefault(root, []).append(record)

    return [group for group in clusters.values() if len(group) > 1]


def rank_group(group):
    return sorted(
        group,
        key=lambda r: (r["width"] * r["height"], r["sharpness"], r["file_size"]),
        reverse=True,
    )


def group_id(group):
    paths = sorted(r["path"] for r in group)
    return hashlib.sha256("|".join(paths).encode()).hexdigest()[:16]
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/test_grouping.py -v`
Expected: PASS (7 tests)

- [ ] **Step 5: Commit**

```bash
git add grouping.py tests/test_grouping.py
git commit -m "feat: add perceptual-hash clustering and keeper ranking"
```

---

### Task 4: Scanner CLI

**Files:**
- Create: `scan.py`
- Test: `tests/test_scan.py`

**Interfaces:**
- Consumes:
  - `db.init_db`, `db.get_cached_entry`, `db.upsert_file`, `db.log_scan_error` (Task 1)
  - `hashing.check_heic_support`, `hashing.MissingHeicSupportError`, `hashing.compute_phash`, `hashing.compute_sharpness`, `hashing.get_metadata` (Task 2)
- Produces:
  - `scan.find_image_files(root: str) -> Iterator[str]`
  - `scan.scan_directory(root: str, conn) -> dict` — keys `scanned`, `skipped`, `errors` (ints).
  - `scan.main(argv=None) -> int` — CLI entry point.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_scan.py`:

```python
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
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_scan.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'scan'`

- [ ] **Step 3: Implement `scan.py`**

```python
import argparse
import os
import sys
from pathlib import Path

import db
import hashing

SUPPORTED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".heic"}
QUARANTINE_DIRNAME = "_duplicates_review"
DB_FILENAME = "photo_deduper.db"


def find_image_files(root):
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d != QUARANTINE_DIRNAME]
        for filename in filenames:
            if Path(filename).suffix.lower() in SUPPORTED_EXTENSIONS:
                yield str(Path(dirpath) / filename)


def scan_directory(root, conn):
    scanned = 0
    skipped = 0
    errors = 0

    for path in find_image_files(root):
        stat = os.stat(path)
        cached = db.get_cached_entry(conn, path, stat.st_size, stat.st_mtime)
        if cached is not None:
            skipped += 1
            continue

        try:
            phash = str(hashing.compute_phash(path))
            sharpness = hashing.compute_sharpness(path)
            metadata = hashing.get_metadata(path, stat.st_mtime)
        except Exception as exc:
            db.log_scan_error(conn, path, exc)
            errors += 1
            continue

        db.upsert_file(
            conn,
            path,
            stat.st_size,
            stat.st_mtime,
            phash,
            metadata["width"],
            metadata["height"],
            stat.st_size,
            metadata["date_taken"],
            sharpness,
        )
        scanned += 1

    return {"scanned": scanned, "skipped": skipped, "errors": errors}


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Scan a directory for photos and cache perceptual hashes."
    )
    parser.add_argument("root", help="Directory to scan")
    args = parser.parse_args(argv)

    try:
        hashing.check_heic_support()
    except hashing.MissingHeicSupportError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    db_path = Path(args.root) / DB_FILENAME
    conn = db.init_db(db_path)
    summary = scan_directory(args.root, conn)
    print(
        f"Scanned {summary['scanned']} new files, "
        f"skipped {summary['skipped']} cached, {summary['errors']} errors."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/test_scan.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add scan.py tests/test_scan.py
git commit -m "feat: add scanner CLI with resumable caching and error handling"
```

---

### Task 5: Review Flask app + template

**Files:**
- Create: `review_app.py`
- Create: `review.py`
- Create: `templates/review.html`
- Test: `tests/test_review_app.py`

**Interfaces:**
- Consumes:
  - `db.init_db`, `db.all_files`, `db.is_group_resolved`, `db.mark_group_resolved` (Task 1)
  - `hashing.compute_phash`, `hashing.compute_sharpness` (Task 2, used only in the test file to seed fixtures)
  - `grouping.group_photos`, `grouping.rank_group`, `grouping.group_id` (Task 3)
- Produces:
  - `review_app.create_app(root: str) -> flask.Flask` — routes `index` (`GET /`), `thumbnail` (`GET /thumbnail`), `resolve` (`POST /resolve`).

- [ ] **Step 1: Write the failing tests**

Create `tests/test_review_app.py`:

```python
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
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_review_app.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'review_app'`

- [ ] **Step 3: Implement `templates/review.html`**

```html
<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <title>Photo Deduper - Review</title>
  <style>
    body { font-family: sans-serif; margin: 2rem; }
    .group { border: 1px solid #ccc; padding: 1rem; margin-bottom: 1.5rem; }
    .photos { display: flex; flex-wrap: wrap; gap: 1rem; }
    .photo { text-align: center; }
    .photo img { max-width: 200px; max-height: 200px; display: block; }
    .photo.suggested { outline: 3px solid #2a7; }
    .meta { font-size: 0.8rem; color: #555; }
    .errors { color: #b00; }
  </style>
</head>
<body>
  <h1>Duplicate groups ({{ groups|length }} remaining)</h1>
  {% with messages = get_flashed_messages() %}
    {% if messages %}
      <ul class="errors">
        {% for message in messages %}
          <li>{{ message }}</li>
        {% endfor %}
      </ul>
    {% endif %}
  {% endwith %}
  {% if not groups %}
    <p>No duplicate groups left to review.</p>
  {% endif %}
  {% for group in groups %}
    <form class="group" method="post" action="{{ url_for('resolve') }}">
      <input type="hidden" name="group_id" value="{{ group.id }}">
      <div class="photos">
        {% for photo in group.photos %}
          <div class="photo{% if loop.first %} suggested{% endif %}">
            <label>
              <input type="checkbox" name="keep" value="{{ photo.path }}" {% if loop.first %}checked{% endif %}>
              <img src="{{ url_for('thumbnail', path=photo.path) }}" alt="">
            </label>
            <div class="meta">
              {{ photo.width }}x{{ photo.height }}<br>
              {{ (photo.file_size / 1024)|round(1) }} KB<br>
              sharpness: {{ photo.sharpness|round(1) }}
            </div>
            <input type="hidden" name="all_paths" value="{{ photo.path }}">
          </div>
        {% endfor %}
      </div>
      <button type="submit">Resolve group (move unchecked to quarantine)</button>
    </form>
  {% endfor %}
</body>
</html>
```

- [ ] **Step 4: Implement `review_app.py`**

```python
import json
import shutil
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path

from flask import Flask, abort, flash, redirect, render_template, request, send_file, url_for
from PIL import Image

import db
import grouping

QUARANTINE_DIRNAME = "_duplicates_review"
DB_FILENAME = "photo_deduper.db"
MOVES_LOG_FILENAME = "moves.log"


def create_app(root):
    root = Path(root).resolve()
    app = Flask(__name__)
    app.secret_key = "photo-deduper-local-dev"  # only used for flash messages on localhost
    app.config["ROOT"] = root
    app.config["DB_PATH"] = root / DB_FILENAME
    app.config["QUARANTINE_DIR"] = root / QUARANTINE_DIRNAME
    app.config["MOVES_LOG"] = root / MOVES_LOG_FILENAME

    @app.route("/")
    def index():
        conn = db.init_db(app.config["DB_PATH"])
        records = db.all_files(conn)
        groups = grouping.group_photos(records)

        unresolved = []
        for group in groups:
            gid = grouping.group_id(group)
            if db.is_group_resolved(conn, gid):
                continue
            unresolved.append({"id": gid, "photos": grouping.rank_group(group)})
        unresolved.sort(key=lambda g: len(g["photos"]), reverse=True)

        return render_template("review.html", groups=unresolved)

    @app.route("/thumbnail")
    def thumbnail():
        requested = Path(request.args.get("path", "")).resolve()
        root_dir = app.config["ROOT"]
        if requested != root_dir and root_dir not in requested.parents:
            abort(403)
        try:
            with Image.open(requested) as img:
                img.thumbnail((300, 300))
                buffer = BytesIO()
                img.convert("RGB").save(buffer, format="JPEG")
                buffer.seek(0)
        except (FileNotFoundError, OSError):
            abort(404)
        return send_file(buffer, mimetype="image/jpeg")

    @app.route("/resolve", methods=["POST"])
    def resolve():
        group_id = request.form["group_id"]
        keep_paths = set(request.form.getlist("keep"))
        all_paths = request.form.getlist("all_paths")

        app.config["QUARANTINE_DIR"].mkdir(exist_ok=True)
        errors = []
        for path_str in all_paths:
            if path_str in keep_paths:
                continue
            source = Path(path_str)
            destination = _unique_destination(app.config["QUARANTINE_DIR"], source)
            try:
                shutil.move(str(source), str(destination))
                _log_move(app.config["MOVES_LOG"], source, destination)
            except OSError as exc:
                errors.append(f"{source}: {exc}")

        if errors:
            for error in errors:
                flash(f"Failed to move {error}")
        else:
            conn = db.init_db(app.config["DB_PATH"])
            db.mark_group_resolved(conn, group_id)

        return redirect(url_for("index"))

    return app


def _unique_destination(quarantine_dir, source):
    destination = quarantine_dir / source.name
    counter = 1
    while destination.exists():
        destination = quarantine_dir / f"{source.stem}_{counter}{source.suffix}"
        counter += 1
    return destination


def _log_move(log_path, source, destination):
    entry = {
        "original_path": str(source),
        "new_path": str(destination),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    with open(log_path, "a") as f:
        f.write(json.dumps(entry) + "\n")
```

- [ ] **Step 5: Implement `review.py`**

```python
import sys

from review_app import create_app


def main(argv=None):
    import argparse

    parser = argparse.ArgumentParser(description="Review and resolve duplicate photo groups.")
    parser.add_argument("root", help="Directory that was scanned")
    args = parser.parse_args(argv)

    app = create_app(args.root)
    app.run(debug=False, port=5000)
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `pytest tests/test_review_app.py -v`
Expected: PASS (5 tests)

- [ ] **Step 7: Commit**

```bash
git add review_app.py review.py templates/review.html tests/test_review_app.py
git commit -m "feat: add review server with duplicate-group resolution"
```

---

### Task 6: README + end-to-end smoke test

**Files:**
- Create: `README.md`
- Create: `scripts/smoke_test.py`

**Interfaces:**
- Consumes: `scan.py` and `review.py` as subprocesses (Tasks 4 and 5) via their CLI interfaces — no direct function imports.
- Produces: nothing consumed by other tasks (this is the last task).

- [ ] **Step 1: Write `scripts/smoke_test.py`**

```python
"""End-to-end smoke test: creates two similar photos in a scratch
folder, runs the scanner, starts the review server, resolves the
duplicate group over HTTP, and confirms the extra copy landed in
quarantine.

Run from the project root with the venv active:
    python scripts/smoke_test.py
"""
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.parse
import urllib.request
from pathlib import Path

from PIL import Image


def main():
    tmp_dir = Path(tempfile.mkdtemp(prefix="photo_deduper_smoke_"))
    try:
        run_smoke_test(tmp_dir)
        print("Smoke test passed.")
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def run_smoke_test(tmp_dir):
    original = tmp_dir / "original.jpg"
    duplicate = tmp_dir / "duplicate.jpg"
    Image.new("RGB", (400, 300), color=(200, 50, 50)).save(original, quality=95)
    Image.open(original).resize((200, 150)).save(duplicate, quality=95)

    scan_result = subprocess.run(
        [sys.executable, "scan.py", str(tmp_dir)], capture_output=True, text=True
    )
    print(scan_result.stdout)
    assert "Scanned 2 new files" in scan_result.stdout, scan_result.stdout

    server = subprocess.Popen([sys.executable, "review.py", str(tmp_dir)])
    try:
        _wait_for_server("http://127.0.0.1:5000/", timeout=10)
        html = urllib.request.urlopen("http://127.0.0.1:5000/").read().decode()
        assert "Resolve group" in html, "expected a duplicate group on the review page"

        group_id = re.search(r'name="group_id" value="([^"]+)"', html).group(1)
        all_paths = re.findall(r'name="all_paths" value="([^"]+)"', html)
        keep_path = all_paths[0]

        body = urllib.parse.urlencode(
            [("group_id", group_id), ("keep", keep_path)]
            + [("all_paths", p) for p in all_paths]
        ).encode()
        urllib.request.urlopen(
            urllib.request.Request("http://127.0.0.1:5000/resolve", data=body, method="POST")
        )

        quarantine_dir = tmp_dir / "_duplicates_review"
        moved = list(quarantine_dir.iterdir())
        assert len(moved) == 1, f"expected 1 file quarantined, found {moved}"
    finally:
        server.terminate()
        server.wait(timeout=5)


def _wait_for_server(url, timeout):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            urllib.request.urlopen(url, timeout=1)
            return
        except Exception:
            time.sleep(0.3)
    raise TimeoutError(f"Server at {url} did not start within {timeout}s")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Run the smoke test**

Run: `python scripts/smoke_test.py`
Expected: prints the scan summary line, then `Smoke test passed.`

- [ ] **Step 3: Write `README.md`**

```markdown
# Photo Deduper

A local tool to find near-duplicate photos on an external drive and
help you pick which copy of each to keep.

## Setup

    python3 -m venv venv
    source venv/bin/activate
    pip install -r requirements.txt

## Usage

1. Scan the directory you want to clean up (this can take a while the
   first time; re-running it later only processes new/changed files):

       python scan.py /Volumes/YourDrive/Photos

2. Start the review server, pointed at the same directory:

       python review.py /Volumes/YourDrive/Photos

3. Open http://127.0.0.1:5000 in your browser. Each duplicate group
   shows thumbnails with resolution, file size, and a sharpness score;
   the suggested keeper is highlighted and pre-checked. Adjust the
   checkboxes if you disagree, then click "Resolve group."

4. Photos you didn't keep are moved (not deleted) into a
   `_duplicates_review/` folder at the root of the scanned directory.
   Every move is also recorded in `moves.log` there, so you can
   manually undo any decision. Once you've spot-checked the
   `_duplicates_review/` folder, delete it yourself when you're ready.

## Smoke test

To verify the whole pipeline works end-to-end on your machine before
pointing it at your real photo library:

    python scripts/smoke_test.py
```

- [ ] **Step 4: Commit**

```bash
git add README.md scripts/smoke_test.py
git commit -m "docs: add README and end-to-end smoke test script"
```
