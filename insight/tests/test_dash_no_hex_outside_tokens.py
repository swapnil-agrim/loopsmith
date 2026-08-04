# SPDX-License-Identifier: BUSL-1.1 - LoopSmith Insight. NOT MIT. See insight/LICENSE.
"""The S7-scoped "zero hex literals outside the token file" test (issue #262, D1, Step 8). Scoped
to insight/dash/ per the issue's own done_when. See .sdlc/plans/262.md Step 8: the regex requires
a CSS-colour-property or SVG-colour-attribute context immediately before the `#`, not a bare
`#[0-9a-fA-F]{3,8}` -- a bare pattern false-positives on every GitHub issue-number reference in
this codebase's own docstrings/comments (`#124`, `#125`, ... `#262`, all decimal, appearing in
prose like "issue #262, D1"). Today there is zero real hex outside colors.py -- this test is a
durability guard against a *future* regression, not a fix for a present one."""
import pathlib
import re

import pytest

_DASH_DIR = pathlib.Path(__file__).resolve().parents[1] / "dash"
_TOKEN_FILES = {"colors.py", "fonts.py"}  # fonts.py is base64 data, trivially hex-free, but
                                           # excluded explicitly rather than by accident
_HEX_IN_CONTEXT = re.compile(
    r'(?:fill|stroke|color|background(?:-color)?|border(?:-color)?)\s*[:=]\s*'
    r'["\']?#[0-9a-fA-F]{3,8}\b',
    re.IGNORECASE,
)


@pytest.mark.parametrize("path", sorted(
    p for p in _DASH_DIR.glob("*.py") if p.name not in _TOKEN_FILES
))
def test_file_declares_no_hex_colour_outside_the_token_file(path):
    text = path.read_text()
    matches = _HEX_IN_CONTEXT.findall(text)
    assert not matches, f"{path.name} hardcodes a hex colour outside colors.py: {matches}"
