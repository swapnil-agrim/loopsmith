import os, subprocess, pathlib, tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_install_copies_hook_and_prints_snippet():
    with tempfile.TemporaryDirectory() as tmp:
        env = {**os.environ, "SDLC_KIT_SKILLS_DIR": tmp}
        proc = subprocess.run(["bash", "install.sh"], cwd=ROOT, env=env,
                              capture_output=True, text=True)
        assert proc.returncode == 0, proc.stderr
        assert (pathlib.Path(tmp) / "loopsmith" / "hooks" / "sdlc_gate.sh").exists()
        # prints the wiring snippet (does NOT edit settings.json itself)
        assert "UserPromptSubmit" in proc.stdout
        assert "sdlc_gate.sh" in proc.stdout
