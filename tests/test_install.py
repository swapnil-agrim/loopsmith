import os, subprocess, pathlib, tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_install_copies_hook_and_prints_snippet():
    with tempfile.TemporaryDirectory() as tmp:
        env = {**os.environ, "SDLC_KIT_SKILLS_DIR": tmp}
        proc = subprocess.run(["bash", "install.sh"], cwd=ROOT, env=env,
                              capture_output=True, text=True)
        assert proc.returncode == 0, proc.stderr
        assert (pathlib.Path(tmp) / "loopsmith" / "hooks" / "sdlc_gate.sh").exists()
        # the spine's skills must actually land, not just the hook
        assert (pathlib.Path(tmp) / "loopsmith" / "skills" / "sdlc-loop" / "scripts" / "loop.py").exists()
        # prints the wiring snippet (does NOT edit settings.json itself)
        assert "UserPromptSubmit" in proc.stdout
        assert "sdlc_gate.sh" in proc.stdout


def test_install_fails_loudly_when_a_copy_fails():
    # A real copy failure must abort (set -e), not be swallowed by `|| true` while the
    # success banner still prints. Force the skills copy to fail by planting a FILE where
    # the skills dir must go (deterministic + root-safe: no permission bits involved).
    with tempfile.TemporaryDirectory() as tmp:
        dest = pathlib.Path(tmp) / "loopsmith"; dest.mkdir()
        (dest / "skills").write_text("blocker")          # cp -R <dir> onto a file -> error
        env = {**os.environ, "SDLC_KIT_SKILLS_DIR": tmp}
        proc = subprocess.run(["bash", "install.sh"], cwd=ROOT, env=env,
                              capture_output=True, text=True)
        assert proc.returncode != 0, "install.sh swallowed a copy failure and exited 0"
