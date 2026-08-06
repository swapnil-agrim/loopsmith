# SPDX-License-Identifier: BUSL-1.1 - LoopSmith Insight. NOT MIT. See insight/LICENSE.
"""issue #303 [E17.S2] review fix 1(5): a mechanical guard that `npm run prove:fonts` stays wired
up, so the browser-dependent font-applied proof cannot silently stop being invocable without a red
test -- the exact defect class this issue exists to fix (the proof was previously folded into `npm
run test` / verify_web.py's CHECKS, which parks every goal in the repo on a machine with no browser;
see verify_web.py's module docstring and insight/web/README.md for the full reasoning behind moving
it to its own script).

This is the package.json HALF of the guard: does insight/web/package.json still declare a
"prove:fonts" script pointing at the real proof script. The other half -- does CI actually invoke
`npm run prove:fonts` -- lives in the repo-root tests/ suite instead of here, because
insight/tests/ must stay extractable to its own repo (see insight/tests/test_standalone_extraction.
py) and cannot depend on .github/workflows/ci.yml, which sits outside insight/ entirely.

Neither half may pass by skipping when its file is missing -- a missing insight/web/package.json
here is a FAILURE, not something to shrug past."""
import json
import pathlib

WEB = pathlib.Path(__file__).resolve().parents[1] / "web"
PACKAGE_JSON = WEB / "package.json"
PROOF_SCRIPT_RELATIVE = "scripts/prove-fonts-actually-apply.mjs"


def test_package_json_declares_a_prove_fonts_script():
    assert PACKAGE_JSON.is_file(), (
        f"{PACKAGE_JSON} not found -- cannot verify the browser-dependent font-applied proof "
        "(prove-fonts-actually-apply.mjs) is wired up at all"
    )
    scripts = json.loads(PACKAGE_JSON.read_text(encoding="utf-8")).get("scripts", {})
    assert "prove:fonts" in scripts, (
        f'{PACKAGE_JSON} "scripts" has no "prove:fonts" entry -- the browser-dependent '
        f'font-applied proof ({PROOF_SCRIPT_RELATIVE}) has silently stopped being invocable. '
        f'Add "prove:fonts": "node {PROOF_SCRIPT_RELATIVE}" back to insight/web/package.json\'s '
        '"scripts", and a step running `npm run prove:fonts` back to .github/workflows/ci.yml\'s '
        "`web` job."
    )
    assert scripts["prove:fonts"] == f"node {PROOF_SCRIPT_RELATIVE}", (
        f'{PACKAGE_JSON}\'s "prove:fonts" script is {scripts["prove:fonts"]!r}, not the expected '
        f'"node {PROOF_SCRIPT_RELATIVE}" -- it must point at the real proof script'
    )


def test_prove_fonts_script_file_exists():
    proof_script = WEB / PROOF_SCRIPT_RELATIVE
    assert proof_script.is_file(), (
        f'insight/web/package.json\'s "prove:fonts" script names {proof_script}, which does not '
        "exist -- the proof script itself has gone missing"
    )
