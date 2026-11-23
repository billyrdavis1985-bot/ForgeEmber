# Local setup: Windows + WSL + VS Code

About 20 minutes the first time. You need no API key until step 7, and nothing costs money until you pass `--live`.

## 1. WSL (Ubuntu)

In **PowerShell as Administrator**:

```powershell
wsl --install -d Ubuntu-24.04
```

Reboot if asked, then open **Ubuntu** from the Start menu and create your Linux user. Every command below runs **in the Ubuntu terminal**, not PowerShell.

```bash
sudo apt update && sudo apt install -y git curl build-essential
```

## 2. VS Code

1. Install VS Code on Windows (code.visualstudio.com).
2. Install the **WSL** extension (`ms-vscode-remote.remote-wsl`).
3. From now on, open the project from the Ubuntu terminal with `code .` (step 4). VS Code then runs inside WSL, and its terminal is a Linux shell.

## 3. Tools: uv, GitHub access, git identity

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh     # uv manages Python 3.12 and the venv
exec $SHELL                                         # reload PATH

sudo apt install -y gh && gh auth login             # GitHub.com > HTTPS > log in with browser
gh auth setup-git                                   # lets git push with that login
```

Your commits must be authored like this (a project rule):

```bash
git config --global user.name "Billy Davis"
git config --global user.email "245758823+billyrdavis1985-bot@users.noreply.github.com"
```

## 4. Clone into the Linux filesystem

Keep the clone under your Linux home, **not** `/mnt/c/...`, which is slow and causes permission trouble.

```bash
mkdir -p ~/code && cd ~/code
git clone https://github.com/billyrdavis1985-bot/ForgeEmber.git
cd ForgeEmber
uv sync              # downloads Python 3.12 if needed and creates .venv
code .               # opens VS Code connected to WSL
```

When VS Code offers the **recommended extensions** (Python, Pylance, Ruff, TOML), install them. The interpreter is preset to `.venv/bin/python`, ruff formats on save, and tests show up in the Testing panel.

## 5. Check the setup (no key, no network)

```bash
python3 scripts/doctor.py      # tools, git identity, venv, key presence
uv run pytest                  # full test suite, fakes only
uv run forgeember budget       # $0.00 of $10.00, $9.00 headroom
```

Fix anything `doctor.py` marks `FAIL` before continuing.

## 6. Meta account safety (one time, in the browser)

- In Meta's **Billing → Spend Limits**, set an alert (e.g. $8.00). It only emails, but it's a second warning on top of our $9.00 cap in code.
- Don't use the Meta **Playground** on the hackathon account. Its usage counts toward the $10, and our ledger can't see it.

## 7. API key (each time you open a new terminal)

Create a key in the Model API dashboard (**API keys → Create**, e.g. named `forgeember-hackathon`). You see it only once. Then, in the terminal where you'll run ForgeEmber:

```bash
read -s MODEL_API_KEY && export MODEL_API_KEY    # paste, then Enter; nothing is echoed
python3 scripts/doctor.py                         # should now say MODEL_API_KEY: set
```

The key lives only in that terminal session. Don't put it in `.bashrc` or a `.env` file, and never paste it into a chat. When you close the terminal, it's gone.

## 8. First live calls (cost fractions of a cent)

```bash
uv run python scripts/smoke_test.py --live --stage dev
uv run forgeember budget
```

Once the Phase 1 PR is merged, screen two fixtures:

```bash
uv run forgeember screen fixtures/invoices/inv-001-fennimore.pdf \
    fixtures/emails/em-001-bank-change-lookalike.eml --stage dev --live
uv run forgeember holds
```

Then publish the numbers and commit them on a branch, never on `main`:

```bash
uv run python scripts/render_budget_readme.py
git checkout -b ledger-update && git add traces README.md docs/assets && git commit -m "Record first live calls"
```

## 9. Muse Code (Spark), in a separate terminal

```bash
curl -fsSL https://dev.meta.ai/install.sh | bash   # from dev.meta.ai/docs/muse-code
cd ~/code/ForgeEmber && muse                       # trust the workspace so it reads AGENTS.md
```

- Use a separate terminal from the one holding `MODEL_API_KEY`, and **don't set `META_API_KEY`** there unless you mean to pay as you go. An exported `META_API_KEY` overrides a subscription sign-in.
- On pay-as-you-go, start it on Ember (`muse --model muse-ember-1.0`), or switch with `/models`. The default, `muse-spark-1.2`, costs 12.5× Ember's input price and 8.5× its output price.
- At the end of each session, run `/export trajectory --out ~/forgeember-trajectories/`. It's needed for the submission.

## Daily loop

```bash
cd ~/code/ForgeEmber && git checkout main && git pull && uv sync
python3 scripts/doctor.py
uv run pytest && uv run ruff check
```
