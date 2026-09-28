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


def scan_directory(root, conn, on_progress=None):
    root = str(Path(root).resolve())
    paths = list(find_image_files(root))
    total = len(paths)
    scanned = 0
    skipped = 0
    errors = 0

    for index, path in enumerate(paths, start=1):
        try:
            stat = os.stat(path)
            cached = db.get_cached_entry(conn, path, stat.st_size, stat.st_mtime)
            if cached is not None:
                skipped += 1
                continue

            phash = str(hashing.compute_phash(path))
            sharpness = hashing.compute_sharpness(path)
            metadata = hashing.get_metadata(path, stat.st_mtime)
        except Exception as exc:
            db.log_scan_error(conn, path, exc)
            errors += 1
            continue
        finally:
            if on_progress is not None:
                on_progress(index, total, path)

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
            metadata["is_screenshot"],
        )
        scanned += 1

    return {"scanned": scanned, "skipped": skipped, "errors": errors}


def _print_progress_bar(current, total, path, width=30):
    if total == 0:
        return
    filled = int(width * current / total)
    bar = "#" * filled + "-" * (width - filled)
    percent = current / total * 100
    name = os.path.basename(path)
    if len(name) > 30:
        name = name[:27] + "..."
    sys.stdout.write(f"\r[{bar}] {current}/{total} ({percent:5.1f}%) {name:<30}")
    sys.stdout.flush()
    if current == total:
        sys.stdout.write("\n")


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

    db_path = Path(args.root).resolve() / DB_FILENAME
    conn = db.init_db(db_path)
    on_progress = _print_progress_bar if sys.stdout.isatty() else None
    summary = scan_directory(args.root, conn, on_progress=on_progress)
    print(
        f"Scanned {summary['scanned']} new files, "
        f"skipped {summary['skipped']} cached, {summary['errors']} errors."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
