import pathlib, importlib.util

S = pathlib.Path(__file__).resolve().parent.parent / "skills" / "sdlc-loop" / "scripts"


def _mod(name):
    spec = importlib.util.spec_from_file_location(name, S / f"{name}.py")
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m); return m


GOAL = "---\nid: 0001\nstatus: pending\nlane: auto\n---\n\nbody text\n"


def test_parse_flat_fields():
    d = _mod("frontmatter").parse(GOAL)
    assert d["id"] == "0001" and d["status"] == "pending" and d["lane"] == "auto"


def test_parse_no_frontmatter_returns_empty():
    assert _mod("frontmatter").parse("just text, no fences") == {}


def test_get_field():
    fm = _mod("frontmatter")
    assert fm.get(GOAL, "status") == "pending" and fm.get(GOAL, "missing") is None


def test_set_field_replaces_in_place_not_body():
    fm = _mod("frontmatter")
    g = GOAL + "status: in the body should be untouched\n"
    out = fm.set_field(g, "status", "done")
    assert fm.get(out, "status") == "done" and "body text" in out
    assert "in the body should be untouched" in out          # body collision safe
