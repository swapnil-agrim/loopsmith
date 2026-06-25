#!/usr/bin/env bash
# Fallback copy-installer for hosts/users not using the Claude Code plugin system.
# Copies the portable spine into the skills dir and PRINTS the hook-wiring snippet
# (never edits settings.json — malformed JSON silently kills the hook).
set -euo pipefail
SRC="$(cd "$(dirname "$0")" && pwd)"
DEST="${SDLC_KIT_SKILLS_DIR:-$HOME/.claude/skills}/loopsmith"

mkdir -p "$DEST"
cp -R "$SRC/hooks" "$DEST/"
# (later slices also copy skills/ and commands/ — guarded so this stays valid now)
[ -d "$SRC/skills" ] && cp -R "$SRC/skills" "$DEST/" || true
[ -d "$SRC/commands" ] && cp -R "$SRC/commands" "$DEST/" || true

cat <<EOF

✅ loopsmith copied to: $DEST

To activate the SDLC hook, add this to ~/.claude/settings.json under "hooks"
(parse-check the file afterward — malformed JSON silently disables hooks):

  "UserPromptSubmit": [
    { "hooks": [
      { "type": "command",
        "command": "bash \"$DEST/hooks/sdlc_gate.sh\"",
        "statusMessage": "Applying goal-based SDLC policy" }
    ] }
  ]

Verify: echo '{"prompt":"x"}' | bash "$DEST/hooks/sdlc_gate.sh" | python3 -m json.tool
EOF
