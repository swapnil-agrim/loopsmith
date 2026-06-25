import pathlib, importlib.util, tempfile

S = pathlib.Path(__file__).resolve().parent.parent / "skills" / "sdlc-loop" / "scripts"


def _disc():
    spec = importlib.util.spec_from_file_location("discovery", S / "discovery.py")
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m); return m


def _goal(d, n, status):
    (pathlib.Path(d) / f"{n}.md").write_text(f"---\nid: {n}\nstatus: {status}\n---\nx\n")


def test_returns_first_pending_in_filename_order():
    with tempfile.TemporaryDirectory() as d:
        _goal(d, "0002", "pending"); _goal(d, "0001", "pending")
        assert _disc().next_pending(d).endswith("0001.md")


def test_skips_done_and_parked():
    with tempfile.TemporaryDirectory() as d:
        _goal(d, "0001", "done"); _goal(d, "0002", "parked"); _goal(d, "0003", "pending")
        assert _disc().next_pending(d).endswith("0003.md")


def test_none_when_all_terminal():
    with tempfile.TemporaryDirectory() as d:
        _goal(d, "0001", "done"); _goal(d, "0002", "parked")
        assert _disc().next_pending(d) is None


def test_ignores_frontmatterless_files():
    with tempfile.TemporaryDirectory() as d:
        (pathlib.Path(d) / "README.md").write_text("# Goals\nno frontmatter\n")
        _goal(d, "0001", "pending")
        assert _disc().next_pending(d).endswith("0001.md")
