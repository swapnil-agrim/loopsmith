# Extracting LoopSmith into its own repository

`loopsmith/` is fully self-contained — it imports nothing outside itself and carries zero
host-project specifics (enforced by `tests/test_self_contained.py`). That's what makes publishing
it a plain copy, with no surgery.

## Steps

1. Copy the directory out, into a fresh location:
   ```bash
   cp -R loopsmith /path/to/loopsmith-repo
   cd /path/to/loopsmith-repo
   ```
2. Initialise and commit:
   ```bash
   git init && git add -A && git commit -m "initial import of loopsmith"
   ```
3. Confirm it stands alone — the tests must pass with no changes, from outside any parent repo:
   ```bash
   python3 -m pytest tests/ -q
   ```
4. Confirm the repo **name, account/org, and visibility (public/private)** before you push — this
   step is public and hard to undo. Then create the GitHub repo and push **from inside this extracted
   copy** (never from a parent repo root — `--source=.` there would target the wrong repository):
   ```bash
   gh repo create <name> --public --source=. --push     # or --private
   ```
5. Users then install it with:
   ```
   /plugin marketplace add <git-url>
   /plugin install loopsmith
   ```

The kit's `pytest tests/ -v` suite must stay green in the new repo with zero edits — that's the
proof the extraction is clean.
