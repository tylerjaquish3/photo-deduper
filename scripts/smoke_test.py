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
