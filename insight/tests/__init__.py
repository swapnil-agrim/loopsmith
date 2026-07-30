# SPDX-License-Identifier: BUSL-1.1 - LoopSmith Insight. NOT MIT. See insight/LICENSE.
"""Tests for the insight package.

These do NOT run under the loop's `verify.command`, which is scoped to `pytest tests/` — see
`.sdlc/config.json` key `verify._command` for why, and issue #96 (E0.S3), which owns widening it
once this package is installable in a fresh worktree. Until then these run only when invoked
directly.
"""
