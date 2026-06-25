import pathlib, importlib.util, tempfile

S = pathlib.Path(__file__).resolve().parent.parent / "skills" / "sdlc-status" / "scripts"


def _status():
    spec = importlib.util.spec_from_file_location("status", S / "status.py")
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m); return m


def test_summary_counts_by_status():
    with tempfile.TemporaryDirectory() as d:
        base = pathlib.Path(d) / ".sdlc"; (base / "goals").mkdir(parents=True); (base / "state").mkdir()
        (base / "state" / "STATE.md").write_text("iteration: 4\n")
        for n, s in [("0001", "done"), ("0002", "parked"), ("0003", "pending")]:
            (base / "goals" / f"{n}.md").write_text(f"---\nid: {n}\nstatus: {s}\n---\nx\n")
        out = _status().summary(str(base))
        assert out["done"] == 1 and out["parked"] == 1 and out["pending"] == 1 and out["iteration"] == 4


def test_summary_counts_quoted_status():   # parity with frontmatter.parse (strips quotes)
    with tempfile.TemporaryDirectory() as d:
        base = pathlib.Path(d) / ".sdlc"; (base / "goals").mkdir(parents=True); (base / "state").mkdir()
        (base / "goals" / "0001.md").write_text('---\nid: 0001\nstatus: "done"\n---\nx\n')
        assert _status().summary(str(base))["done"] == 1
