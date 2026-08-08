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


# --- the unterminated private-key fallback (#534) -----------------------------------------------
# The well-formed BEGIN..END form belongs to the FIRST pattern; everything below pins the SECOND one,
# the fallback for a key whose END marker never arrived (truncated at the source, or a body that was
# cut mid-capture). It used to replace the header alone and let every following body line through.
# `_B64_LINE` is non-secret filler shaped like a real PEM body line (a 64-char base64 run).
_B64_LINE = "MIIBVwIBADANBgkqhkiG9w0BAQEFAASCATkwggI1AgEAAoIBAQDBn4t3sQ2K9xVq"


def test_scrub_redacts_the_body_of_an_unterminated_private_key():
    scrub = _mod("scrub").scrub
    out = scrub("intro\n-----BEGIN PRIVATE KEY-----\n" + _B64_LINE + "\n" + _B64_LINE + "\n")
    assert _B64_LINE not in out and "[REDACTED:private-key]" in out
    assert "intro" in out                       # only the key is consumed, not what preceded it


def test_scrub_redacts_an_unterminated_encrypted_pem_including_its_headers():
    # RFC1421 encrypted PEM puts Proc-Type:/DEK-Info: BETWEEN the header and the body, so a fallback
    # that consumed base64 runs alone would stop at them and leave the whole body underneath.
    scrub = _mod("scrub").scrub
    out = scrub("-----BEGIN RSA PRIVATE KEY-----\nProc-Type: 4,ENCRYPTED\n"
                "DEK-Info: AES-128-CBC,1B35E2A9F0C4D7E8\n\n" + _B64_LINE + "\n" + _B64_LINE + "\n")
    assert _B64_LINE not in out and "DEK-Info" not in out
    assert "[REDACTED:private-key]" in out


def test_scrub_redacts_a_short_final_fragment_of_a_truncated_key():
    # the exact artifact of a capped capture: the last body line is cut mid-run, leaving a <16-char
    # remainder at end-of-input. Consumed only THERE — never mid-document (see the residual pin below).
    scrub = _mod("scrub").scrub
    assert scrub("-----BEGIN PRIVATE KEY-----\n" + _B64_LINE + "\nSHORTFRAG") == "[REDACTED:private-key]"


def test_scrub_leaves_prose_following_a_bare_private_key_header():
    # a document merely DISCUSSING the marker keeps its text: the guard against a consume-to-EOF fallback
    scrub = _mod("scrub").scrub
    assert (scrub("-----BEGIN PRIVATE KEY----- is the marker format used by PEM files.")
            == "[REDACTED:private-key] is the marker format used by PEM files.")


def test_scrub_terminated_key_is_still_owned_by_the_first_pattern():
    # ordering guard: the DOTALL BEGIN..END pattern must stay ABOVE the fallback, so a well-formed key
    # is matched as one unit and the greedy body run can never eat an intervening END marker.
    scrub = _mod("scrub").scrub
    out = scrub("-----BEGIN PRIVATE KEY-----\n" + _B64_LINE + "\n-----END PRIVATE KEY-----\ntrailer")
    assert out == "[REDACTED:private-key]\ntrailer"


# --- ACCEPTED LIMITATIONS of the fallback (#534) ------------------------------------------------
# Consumption walks recognized headers and long base64 runs and STOPS at the first sub-16-char run or
# non-base64 byte inside the body; everything from that gap onward survives, and it is NOT bounded to
# a fixed number of characters. The three tests below pin that honestly — including the part that
# stings — so the next reader meets the real behavior instead of a comforting bound. All three are
# pins on CURRENT behavior, not red-first: each documents an exposure being accepted, and each must
# be revisited deliberately if the run rule or the header list is ever widened.

def test_scrub_accepted_limitation_a_short_run_mid_document_stops_consumption():
    """A <16-char base64 run that is NOT at end-of-input stops the walk. Consuming it would eat the
    ordinary short words that follow a header mid-document. Here the remainder happens to be small —
    the next test covers the case where it is not, which is the one that matters."""
    scrub = _mod("scrub").scrub
    out = scrub("-----BEGIN PRIVATE KEY-----\n" + _B64_LINE + "\nSHORTFRAG\nmore prose here.")
    assert _B64_LINE not in out                 # consumed up to the gap...
    assert "SHORTFRAG" in out                   # ...and the walk stops there


def test_scrub_accepted_limitation_a_mangled_body_publishes_everything_after_the_gap():
    """THE RESIDUAL IS UNBOUNDED, and this is the test that says so. A foreign byte partway down the
    body splits a line into two sub-16-char runs, the walk stops at the first of them, and every
    COMPLETE key line after that point is published — 128 bytes of body here, arbitrarily more in a
    longer key. So the fallback's real guarantee is narrower than "an unterminated key is redacted":
    it fully consumes a CANONICAL unterminated key (a clean body cut short at the source — the case
    it exists for, pinned above), and a MANGLED body only as far as the gap."""
    scrub = _mod("scrub").scrub
    mangled = _B64_LINE[:8] + "!" + _B64_LINE[8:]        # one non-base64 byte, mid-line
    out = scrub("-----BEGIN PRIVATE KEY-----\n" + _B64_LINE + "\n" + mangled
                + "\n" + _B64_LINE + "\n" + _B64_LINE + "\n")
    assert out.startswith("[REDACTED:private-key]")      # header + PRE-gap body ARE consumed
    assert out.count(_B64_LINE) == 2                     # the two POST-gap lines SURVIVE — accepted


def test_scrub_accepted_limitation_an_unrecognized_header_line_stops_consumption():
    """The header list is closed on purpose: RFC1421 defines only Proc-Type and DEK-Info, and OpenSSH
    keys carry no headers at all, so matching `Word:` open-endedly would eat ordinary prose. The cost
    is that an unrecognized header between BEGIN and the body — `Comment:` is the one seen in the
    wild — halts the walk and leaves the entire body behind it."""
    scrub = _mod("scrub").scrub
    out = scrub("-----BEGIN PRIVATE KEY-----\nComment: my key\n" + _B64_LINE + "\n" + _B64_LINE + "\n")
    assert out.startswith("[REDACTED:private-key]")      # the marker is still replaced...
    assert out.count(_B64_LINE) == 2                     # ...but the body survives — accepted


def test_secret_patterns_stay_in_sync_with_the_research_capture_hook():
    """scrub.py deliberately duplicates the hook's patterns (a skill script can't reliably import a
    hook across the plugin layout). Without this, the next hardening of research_capture.py leaves
    scrub.py behind — quietly weakening the mirror's redaction. Parity, enforced."""
    scrub = _mod("scrub")
    rc_path = pathlib.Path(__file__).resolve().parent.parent / "hooks" / "research_capture.py"
    spec = importlib.util.spec_from_file_location("research_capture", rc_path)
    rc = importlib.util.module_from_spec(spec); spec.loader.exec_module(rc)
    mine = [(p.pattern, p.flags, repl) for p, repl in scrub._SECRET_PATTERNS]     # flags too: a
    theirs = [(p.pattern, p.flags, repl) for p, repl in rc._SECRET_PATTERNS]      # DOTALL-only drift counts
    assert mine == theirs, "scrub.py and research_capture.py secret patterns drifted — re-sync them"
