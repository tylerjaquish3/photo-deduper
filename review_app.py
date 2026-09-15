import json
import shutil
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path

from flask import Flask, abort, flash, redirect, render_template, request, send_file, url_for
from PIL import Image

import db
import grouping
import hashing  # noqa: F401  (imported for its HEIC-opener registration side effect)

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
        if not _is_within_root(requested, root_dir):
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
        root_dir = app.config["ROOT"]

        if not keep_paths:
            flash("Select at least one photo to keep before resolving.")
            return redirect(url_for("index"))

        for path_str in all_paths:
            if not _is_within_root(Path(path_str).resolve(), root_dir):
                flash(f"Rejected: {path_str} is outside the scanned directory.")
                return redirect(url_for("index"))

        app.config["QUARANTINE_DIR"].mkdir(exist_ok=True)
        conn = db.init_db(app.config["DB_PATH"])
        errors = []
        for path_str in all_paths:
            if path_str in keep_paths:
                continue
            source = Path(path_str)
            if not source.exists():
                # Already moved on a prior (partially failed) attempt — the
                # cache row is stale, but there's nothing left to move.
                db.delete_file(conn, path_str)
                continue
            destination = _unique_destination(app.config["QUARANTINE_DIR"], source)
            try:
                shutil.move(str(source), str(destination))
                _log_move(app.config["MOVES_LOG"], source, destination)
                db.delete_file(conn, path_str)
            except (OSError, shutil.Error) as exc:
                errors.append(f"{source}: {exc}")

        if errors:
            for error in errors:
                flash(f"Failed to move {error}")
        else:
            db.mark_group_resolved(conn, group_id)

        return redirect(url_for("index"))

    return app


def _is_within_root(path, root_dir):
    return path == root_dir or root_dir in path.parents


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
