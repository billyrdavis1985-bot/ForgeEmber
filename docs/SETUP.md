# Project setup

Assumes Billy's existing machine: WSL (Ubuntu) with VS Code, and the repo cloned inside WSL. Nothing here costs money until you pass `--live`.

**Run every command in a WSL (Ubuntu) terminal, not PowerShell.** If your prompt starts with `PS C:\`, type `wsl` and press Enter first; the prompt should end in `$`. In VS Code, `code .` from that WSL terminal opens a window whose terminal is already WSL.

## 1. Get the latest code and the Python environment

```bash
cd ~/path/to/ForgeEmber            # your existing clone
git checkout main && git pull
command -v uv || { curl -LsSf https://astral.sh/uv/install.sh | sh; source $HOME/.local/bin/env; }
uv sync                            # creates .venv with Python 3.12 from uv.lock
code .
```

VS Code uses `.venv/bin/python` from `.vscode/settings.json`, formats with Ruff on save, and lists tests in the Testing panel. Accept the recommended extensions if it offers them.

## 2. Check it (no key, no network)

```bash
python3 scripts/doctor.py          # tools, git identity, venv, key presence (never prints the key)
uv run pytest
uv run forgeember budget           # $0.00 of $10.00, $9.00 headroom
```

## 3. Key and first live call

Set the key in each new terminal session. It's never saved to a file or pasted into chat:

```bash
read -s MODEL_API_KEY && export MODEL_API_KEY
uv run python scripts/smoke_test.py --live --stage dev
uv run forgeember budget
```

## 4. Muse Code (Spark)

Run it in a separate terminal from the one holding `MODEL_API_KEY`, and don't export `META_API_KEY` there: it overrides a subscription sign-in and bills per token. On pay-as-you-go, use `--model muse-ember-1.0`. Export `/export trajectory` at the end of each session.
