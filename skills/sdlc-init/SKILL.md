---
name: sdlc-init
description: Scaffold the per-project .sdlc/ layer (project stub, goals, config, state) for the SDLC kit. Use when the user runs /sdlc-init or asks to initialize or set up the SDLC kit in a repository.
allowed-tools: Bash(python3 *)
---

# sdlc-init

Scaffold the `.sdlc/` project layer, then report what happened.

1. Run the bundled scaffolder from the repository root:

   `python3 "${CLAUDE_SKILL_DIR}/scripts/sdlc_init.py"`

   (Pass a target path as the first argument, and pass through any `--github` / `--demo` flags the
   user gave. **`--github`** also installs the GitHub PM scaffolding — epic/task/bug issue templates,
   the auto-add-to-project workflow, a critical-insight template, and a label guide — into `.github/`.
   **`--demo`** queues a small, safe, runnable demo goal so `/sdlc-loop` shows the SDLC immediately;
   with `--github`, also file it as an `sdlc:goal` issue (`gh issue create`) so it runs on the board.)
2. Read the printed `created / skipped` summary and the git tip.
3. Report which files were created. If `.sdlc/project.md` was newly created, prompt the
   user to fill its **Verify command** and **Stack** sections before running goals.
4. Relay the git tip. Never overwrite a file the scaffolder reports as skipped — those hold live state.
