# Empty on purpose: this file's presence at the repo root makes pytest treat
# this directory as the rootdir and add it to sys.path, so `import db`,
# `import scan`, etc. in tests/ resolve correctly whether pytest is invoked
# as `pytest`, `./venv/bin/pytest`, or `python -m pytest` from anywhere in
# the repo.
