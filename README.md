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
