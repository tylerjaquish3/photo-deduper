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
    root = str(Path(root).resolve())
    scanned = 0
    skipped = 0
    errors = 0

    for path in find_image_files(root):
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

    db_path = Path(args.root).resolve() / DB_FILENAME
    conn = db.init_db(db_path)
    summary = scan_directory(args.root, conn)
    print(
        f"Scanned {summary['scanned']} new files, "
        f"skipped {summary['skipped']} cached, {summary['errors']} errors."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
