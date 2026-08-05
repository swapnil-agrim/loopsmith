---
id: 0298
title: Freeze the engine-product data contract, with golden fixtures
lane: large
source: github
status: in_progress
verify_command: python3 -m pytest -q tests/ && python3 -m pytest -q insight/tests/ && python3 insight/verify_web.py
done_when: golden fixtures exist under insight/contract/ and both sides pass independently
---
Body text is not part of the frontmatter contract; only the fenced block above is.
