# Photo Deduper — Design Spec

Date: 2026-09-15

## Purpose

A local tool to scan a directory of photos on an external hard drive,
find near-duplicate photos (exact copies, resized/re-exported versions,
near-identical burst shots, slightly cropped/edited copies), and let
the user review each duplicate group in a browser UI to pick which
copy to keep. Rejected copies are moved to a quarantine folder rather
than deleted, so the operation is reversible.

Scale target: one-time cleanup of a large library (10k+ photos).
Repeat runs (e.g. after fixing an issue) should not need to
reprocess already-scanned files.

## Non-goals

- Not a general photo management/organization app (no albums, tagging,
  search).
- Not designed for continuous/ongoing incremental scanning as new
  photos are added over time (though the caching behavior happens to
  make that cheap — it's just not a designed use case here).
- Not cross-platform tested — built and used on macOS only.

## Architecture

Two Python entry points sharing one local SQLite cache file
(`photo_deduper.db`), all running entirely on the local machine:

1. **Scanner** (`scan.py`) — a CLI command that walks a target
   directory (the external drive's photo folder), computes a
   perceptual hash and quality metadata for each photo, and writes
   results to the SQLite cache.
2. **Review server** (`review.py`) — a local Flask app that reads the
   cache, clusters photos into duplicate groups, and serves a
   browser-based review UI at `localhost:5000`.

No files on the scanned drive are modified during scanning. Files are
only moved during the "resolve a group" action in the review UI.

## Components & data flow

### Scanning

- Recursively walks the target directory for image files with
  extensions: `.jpg`, `.jpeg`, `.png`, `.heic`.
- HEIC support requires the `pillow-heif` package (registered as a
  Pillow plugin at startup). If missing, the scanner exits early with
  an instruction to `pip install pillow-heif` rather than failing
  partway through a long scan.
- For each file, the cache key is `(path, size, mtime)`. If a row
  already exists in the DB with a matching size and mtime, the file is
  skipped — this is what makes re-running the scanner after the first
  full pass fast.
- For each new/changed file, the scanner computes and stores:
  - `phash` — perceptual hash via `imagehash.phash` (64-bit), robust to
    resizing, re-encoding, and minor edits.
  - `width`, `height` — image resolution.
  - `file_size` — bytes on disk.
  - `date_taken` — from EXIF `DateTimeOriginal` if present, else file
    mtime.
  - `sharpness` — variance of the Laplacian (via OpenCV) as a rough
    blur/quality signal; higher is sharper.
- Corrupt or unreadable files are caught, logged to a `scan_errors`
  table (path + error message), and skipped — one bad file never
  aborts the scan.
- If the drive disconnects or the scan is interrupted, already-cached
  files remain valid; re-running `scan.py` resumes by only processing
  what's left.

### Grouping

- Run at review-server startup (and re-run whenever the review page is
  reloaded, so newly resolved groups drop out).
- Loads all cached `phash` values, computes pairwise Hamming distance
  using a vectorized numpy comparison, and unions any pair within a
  configurable threshold (default: distance ≤ 8 out of 64 bits) using
  union-find.
- Groups of size 1 (no duplicates found) are discarded.
- This pairwise approach is O(n²) but vectorized, which comfortably
  handles the 10k+ photo target in low single-digit seconds. If a
  future library grows far beyond that (~100k+), this would need a
  smarter index (e.g. a BK-tree) — out of scope for now.
- Each group gets a stable `group_id` (hash of its sorted file paths)
  so a group already marked resolved is recognized as resolved even if
  grouping is recomputed.

### Ranking (suggested keeper)

Within a group, photos are sorted by:
1. Resolution (width × height), descending
2. Sharpness score, descending
3. File size, descending

The top result is pre-highlighted in the UI as the suggested keeper,
but the user makes the final choice.

### Review UI

- Flask app serving one page: a list of unresolved duplicate groups,
  largest groups first.
- Each group renders as a row of thumbnails (generated on the fly,
  cached to a temp dir) with resolution, file size, sharpness score,
  and date taken shown under each photo. The suggested keeper is
  visually highlighted.
- The user clicks to select one or more photos to keep, then clicks
  "Resolve group."
- On resolve:
  - Every non-selected photo in the group is moved via `shutil.move`
    into `_duplicates_review/` at the root of the scanned drive
    (created if absent), preserving the original filename (with a
    numeric suffix on collision).
  - Each move is appended as a line to `moves.log` (JSON: original
    path, new path, timestamp) — a durable, human-readable undo trail
    independent of the SQLite cache.
  - The group is marked `resolved` in the cache and disappears from
    the review list.
  - If a move fails (permissions, drive unplugged), that file's
    failure is reported inline in the UI and the group stays
    unresolved so the user can retry.

## Error handling summary

| Failure | Behavior |
|---|---|
| Corrupt/unreadable image during scan | Logged to `scan_errors`, file skipped, scan continues |
| HEIC support not installed | Scanner exits immediately at startup with install instructions |
| Drive disconnects mid-scan | Scan stops cleanly; cached progress preserved; re-run resumes |
| File move fails during resolve | Reported in UI per-file; group left unresolved for retry |

## Testing

- Pytest unit tests for the pure logic: hashing, Hamming-distance
  grouping, and ranking. Test fixtures generate a small set of sample
  images (an original, a resized copy, a rotated copy, an unrelated
  image) to confirm the resized/rotated copies cluster together and
  the ranking prefers the higher-resolution one.
- The Flask review UI and file-move behavior are verified with a
  manual smoke test against a small sample folder before running
  against the real external drive. No automated browser/UI tests —
  not warranted for a single-user local utility.

## Project setup

- New standalone project at `~/sites/photo-deduper`, own git repo,
  unrelated to the tylerwebco codebase.
- Dependencies: `Pillow`, `pillow-heif`, `imagehash`, `opencv-python`,
  `numpy`, `Flask`.
