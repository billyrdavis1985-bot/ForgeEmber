# AGENTS.md: working rules for ForgeEmber

For **Muse Code (Spark)** and **Claude Code** alike (Claude Code loads this via `CLAUDE.md`). Billy Davis is the only human builder. He holds the API key and merges.

**Default: build.** Make reasonable calls yourself, say what you decided in the PR description, and keep going. Ask Billy only for the things listed under "Ask first".

## Hard rules (break one and we risk disqualification, money or safety)
- **Spend:** a $9.00 hard cap in code against a $10.00 grant. Only `ember/client.py` calls the API, always through the ledger, always with an explicit `reasoning_effort` (Ember defaults to `max`). Live calls need `--live` plus a stage, and only Billy runs them. Never in CI.
- **Secrets:** never read, print, log or commit keys, `.env` or environment variables. Never ask for the key in chat.
- **Authorship:** commits are authored as `Billy Davis <245758823+billyrdavis1985-bot@users.noreply.github.com>`, with no `Co-Authored-By` or AI trailers. AI help is disclosed in the README.
- **Originality and data:** new code only (nothing from ForgeVigil). Use a dataset only after its license is checked at the source and listed in `docs/DATA.md`.
- **Product:** Muse Ember only. Ember proposes and code decides: T2 actions (payments, bank changes, outside sends, deletions) are always held, never executed.
- **Git:** never push to `main`, force-push or tag. Feature branches and PRs are fine.

## Ask first
Spending money or moving budget between stages, changing scope or a hard rule, adding a dataset, or anything that can't be undone.

## Lightweight process
- Small PRs. Merge when CI is green. Cross-review (Spark ↔ Claude) is for changes to budget, policy, verify or security code. Elsewhere it's optional.
- `docs/BUILD_LOG.md`: one short entry per day or milestone (what changed, spend, next).
- Docs change only when behavior changes. Don't write new docs nobody asked for.
- Keep the README budget block generated (`scripts/render_budget_readme.py`), never hand-edited.

## Commands
```bash
uv sync && uv run pytest && uv run ruff check   # setup + checks
python3 scripts/doctor.py                       # environment check (never prints the key)
uv run forgeember budget                        # spend report
uv run python scripts/render_budget_readme.py   # refresh README budget block
```
