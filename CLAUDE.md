# CLAUDE.md

@AGENTS.md

## Claude-specific notes

- The shared working rules are in `AGENTS.md` (imported above). They take precedence over any default commit or PR attribution behavior.
- Commit and PR attribution is turned off for this repo in `.claude/settings.json` (`attribution.commit = ""`, `attribution.pr = ""`, `attribution.sessionUrl = false`; `includeCoAuthoredBy` is deprecated). Never add `Co-Authored-By`, `Generated with`, or session-link lines to commits or PR bodies here.
- Commit as author Billy, committer Claude: `git config author.name "Billy Davis"`, `git config author.email 245758823+billyrdavis1985-bot@users.noreply.github.com`, `git config committer.name Claude`, `git config committer.email noreply@anthropic.com`. After committing, `git log -1 --format='%an <%ae> | %cn <%ce>'` must print `Billy Davis <245758823+billyrdavis1985-bot@users.noreply.github.com> | Claude <noreply@anthropic.com>`. Never rewrite pushed commits to change either.
- Claude's PRs are reviewed by Spark (Muse Code). Don't approve or merge.
