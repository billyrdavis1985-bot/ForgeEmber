# AGENTS.md: working rules for every AI coding assistant on ForgeEmber

These rules apply to **Muse Code (Muse Spark)** and **Claude Code** alike. Claude Code loads them through `CLAUDE.md`. Billy Davis is the only human builder and the operator: he approves plans, holds the API key and merges every PR.

Start every session by reading `docs/INTENT.md` and the latest entry in `docs/BUILD_LOG.md`.

## Git
- **Every commit is authored as `Billy Davis <245758823+billyrdavis1985-bot@users.noreply.github.com>`.** Set it per repo: `git config user.name "Billy Davis"` and `git config user.email "245758823+billyrdavis1985-bot@users.noreply.github.com"`.
- **No `Co-Authored-By` or other attribution trailers** in commits or PR bodies. AI assistance is disclosed in the README instead.
- Work on feature branches. You may push feature branches and open PRs.
- **Never push to `main`, never merge, never force-push, never tag or release.** Billy merges.

## Review
- Whoever writes a PR doesn't review it. Claude's PRs are reviewed by Spark (Muse Code), Spark's by Claude, and Billy makes the final call.

## Secrets
- The key lives only in Billy's shell as `MODEL_API_KEY`. He sets it himself with `read -s MODEL_API_KEY && export MODEL_API_KEY`.
- **Never read, print, log or commit `.env` or environment variables. Never ask Billy to paste the key into chat.**

## Live calls and money
- Only `src/forgeember/ember/client.py` calls the API, and only through the budget ledger. A test enforces that no other module imports a network library.
- Tests use recorded fixtures or a fake transport. **CI never makes live calls.** The client refuses live HTTP when `CI` is set.
- A live call needs an explicit `--live` flag **and** a stage name. Only Billy runs live commands.
- The hard cap is **$9.00** of a $10.00 grant. Stages: dev $1.50, eval $1.50, scale $4.00, demo $0.75, reserve $1.25. Moving money between stages needs Billy's approval (`--approved-by`) and a BUILD_LOG entry. **Never raise the cap.**
- Every Ember call must pass an explicit `reasoning_effort`. Ember defaults to `"max"`, the most expensive level.
- Don't use the Meta Playground or run Muse Code on pay-as-you-go against the hackathon account without telling Billy: that spend is invisible to our ledger.

## Code and data
- **No code from ForgeVigil** or any earlier project. Write everything fresh. Ideas carry over; code doesn't.
- Verify every dataset's license at its primary source and record it in `docs/DATA.md` before downloading. If it's unclear, don't use it.
- Raw corpora go in `data/` (gitignored). The demo uses only our own fictional fixtures.
- Verify Meta API parameters against `docs/META_API_NOTES.md`, or Meta's docs, before building on them. Never build on anything marked UNVERIFIED.
- Ember proposes; code decides. Only `policy.py` approves actions. T2 actions (payments, bank-detail changes, outside sends, deletions) are always held for Billy.

## Process
- **Escalate, don't route around.** If a rule, constraint or missing fact blocks the goal, stop and ask Billy. Don't quietly invent a workaround or shrink the scope. Record each decision in `docs/BUILD_LOG.md`.
- **Governance budget:** gates apply to payments, outside sends, spend and merges. Everything else moves fast. Keep process overhead to about 15% of effort.
- **Build log:** after every session, append a dated entry to `docs/BUILD_LOG.md`: who worked (Claude, Spark or Billy), what changed, decisions made, spend delta and next steps. This is the handoff between sessions.
- **Judge-facing from day one:** keep the README's submission overview current. Regenerate the budget block with `uv run python scripts/render_budget_readme.py`; never edit it by hand.

## Commands
Setup: `docs/SETUP.md`, then `python3 scripts/doctor.py`.

```bash
uv sync                                        # install
python3 scripts/doctor.py                      # setup check (no network, never prints the key)
uv run pytest                                  # tests (fakes only)
uv run ruff check && uv run ruff format --check
uv run forgeember budget                       # budget report
uv run python scripts/render_budget_readme.py  # refresh README budget block
```
