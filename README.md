# SDLC ledger — ops branch

Machine-written. **Never merged into the integration branch** and never runs CI: this is a
coordination ledger, not code.

`entries/<actor>.jsonl` is append-only and single-writer — each person writes only their own file,
so concurrent appends cannot conflict. `TEAM.md` is generated; regenerate it rather than resolving
it by hand.

Checked out as a worktree at `.sdlc/ledger/` in each person's clone, so pulling it never disturbs
their code checkout.
