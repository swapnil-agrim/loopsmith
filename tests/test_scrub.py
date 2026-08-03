"""The shared secret-shaped redactor used by the board mirror + cross-check (scrub.py). Location-only
capture rule: a match becomes a typed placeholder, never the value. Deterministic, $0."""
import pathlib, importlib.util

S = pathlib.Path(__file__).resolve().parent.parent / "skills" / "sdlc-loop" / "scripts"


def _mod(name):
    spec = importlib.util.spec_from_file_location(name, S / f"{name}.py")
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m); return m


def test_scrub_redacts_shape_tokens_and_never_emits_the_value():
    scrub = _mod("scrub").scrub
    for secret in ("AKIAABCDEFGHIJKLMNOP", "ghp_" + "a" * 30,
                   "eyJhbGciOiJIUzI1.eyJzdWIiOiIxMjM.SflKxwRJSMeKKF2QT4"):
        out = scrub("prefix " + secret + " suffix")
        assert secret not in out and "REDACTED" in out


def test_scrub_catches_secret_glued_to_a_word_char():
    # the reason the shape patterns carry NO \b anchor: a secret glued to a preceding char must not slip
    scrub = _mod("scrub").scrub
    assert "AKIAABCDEFGHIJKLMNOP" not in scrub("id=AKIAABCDEFGHIJKLMNOP")


def test_scrub_key_value_assignment_redacts_value_keeps_key():
    scrub = _mod("scrub").scrub
    out = scrub('password: "hunter2xyz"')
    assert "hunter2xyz" not in out and "password" in out


def test_scrub_does_not_over_redact_ordinary_prose():
    # bare "token" is deliberately not keyworded — it must survive in ordinary text
    scrub = _mod("scrub").scrub
    assert scrub("we spent a lot of token budget today") == "we spent a lot of token budget today"


def test_scrub_empty_is_passthrough():
    scrub = _mod("scrub").scrub
    assert scrub("") == "" and scrub(None) is None
