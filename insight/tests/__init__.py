# SPDX-License-Identifier: BUSL-1.1 - LoopSmith Insight. NOT MIT. See insight/LICENSE.
"""Tests for the insight package.

These now run under the loop's `verify.command` too (widened by issue #96, E0.S3, once this
package became installable in a fresh worktree) — see `.sdlc/config.json` key `verify._command`
for the exact command and its reasoning. CI runs them again independently, in their own `insight`
job with their own `--cov=insight` floor (`.github/workflows/ci.yml`), so a failure here never
fails the plugin's `test` job and never enters its `--cov=skills --cov=hooks` denominator.
"""
