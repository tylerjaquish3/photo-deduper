# Photo Deduper

A local tool to find near-duplicate photos on an external drive and
help you pick which copy of each to keep.

## Installing Python

Skip this if `python3 --version` (macOS/Linux) or `python --version`
(Windows) already prints a version number.

### Windows

Easiest: open Git Bash or PowerShell and run:

    winget install Python.Python.3.12

Restart your terminal afterward so it picks up the new PATH, then check
with `python --version`.

If `winget` isn't available, download the installer from
https://python.org/downloads instead, run it, and check **"Add python.exe
to PATH"** on the first screen before clicking Install Now.

Either way, afterward go to Windows Settings > Apps > Advanced app
settings > App execution aliases and turn **off** the `python.exe` /
`python3.exe` toggles — Windows ships placeholder stubs under those names
that redirect to the Microsoft Store, and they can shadow the real
install you just added to PATH (this shows up as `bash: .../WindowsApps/
python3: Permission Denied` in Git Bash).

### macOS

Usually already installed. If `python3 --version` fails, install via
[Homebrew](https://brew.sh): `brew install python3`.

## Setup

### macOS / Linux

    python3 -m venv venv
    source venv/bin/activate
    pip install -r requirements.txt

If `pip install` fails with an SSL certificate error, you're likely behind
a corporate TLS-inspecting proxy (e.g. Zscaler). Find your proxy's root
cert (on this machine: `/etc/ssl/certs/zscaler_root_and_inter.pem`) and
install with `PIP_CERT=/path/to/cert.pem pip install -r requirements.txt`.

### Windows (Git Bash)

    python -m venv venv
    source venv/Scripts/activate
    pip install -r requirements.txt

Use `python`, not `python3` — Windows Python installs from python.org don't
create a `python3` command. If `python -m venv venv` fails with something
like `bash: .../WindowsApps/python3: Permission Denied` or opens the
Microsoft Store, there's no real Python installed — only the Store's
placeholder alias. Install Python from https://python.org/downloads
(check "Add python.exe to PATH" during setup), then in Windows Settings go
to Apps > Advanced app settings > App execution aliases and turn off the
`python.exe`/`python3.exe` toggles so the placeholder stops intercepting the
command. Close and reopen Git Bash afterward so it picks up the new PATH.

(Using PowerShell instead of Git Bash? Activate with
`venv\Scripts\Activate.ps1`, or `venv\Scripts\activate.bat` in cmd.exe.)

## Usage

1. Scan the directory you want to clean up (this can take a while the
   first time; re-running it later only processes new/changed files):

       python scan.py /Volumes/YourDrive/Photos    # macOS
       python scan.py "D:/Photos"                  # Windows

2. Start the review server, pointed at the same directory:

       python review.py /Volumes/YourDrive/Photos    # macOS
       python review.py "D:/Photos"                  # Windows

3. Open http://127.0.0.1:5151 in your browser. Each duplicate group
   shows thumbnails with resolution, file size, sharpness score, and
   date taken; the suggested keeper is highlighted and pre-checked.
   Adjust the checkboxes if you disagree, then click "Resolve group."
   (The server defaults to port 5151 instead of 5000 since 5000
   collides with macOS AirPlay Receiver; pass `--port` to change it.)

4. Photos you didn't keep are moved (not deleted) into a
   `_duplicates_review/` folder at the root of the scanned directory.
   Every move is also recorded in `moves.log`, next to it at the root
   of the scanned directory, so you can manually undo any decision.
   Once you've spot-checked the `_duplicates_review/` folder, delete
   it yourself when you're ready.

## Tests

Run the test suite:

    pytest

## Smoke test

To verify the whole pipeline works end-to-end on your machine before
pointing it at your real photo library:

    python scripts/smoke_test.py
