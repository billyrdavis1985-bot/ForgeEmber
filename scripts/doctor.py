"""Check a ForgeEmber dev environment before anything costs money. No network, no secrets shown.

    python3 scripts/doctor.py        # works before `uv sync` (standard library only)

It checks the tools, your git identity, the virtual environment, and whether the API key is
present. It never prints the key: it reports only "set" or "not set", and whether the value
has the shape of a Meta key. Exit code 1 if any check FAILs.
"""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
import sys
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path

REQUIRED_NAME = "Billy Davis"
REQUIRED_EMAIL = "245758823+billyrdavis1985-bot@users.noreply.github.com"
OK, WARN, FAIL = "ok", "warn", "FAIL"


@dataclass(frozen=True)
class Result:
    status: str
    check: str
    detail: str


def find_root(start: Path) -> Path | None:
    for candidate in (start, *start.parents):
        pyproject = candidate / "pyproject.toml"
        if pyproject.is_file() and 'name = "forgeember"' in pyproject.read_text(encoding="utf-8"):
            return candidate
    return None


def _git_config(root: Path, key: str) -> str:
    out = subprocess.run(
        ["git", "-C", str(root), "config", "--get", key],
        capture_output=True,
        text=True,
        check=False,
    )
    return out.stdout.strip()


def _git_tracked(root: Path, path: str) -> bool:
    out = subprocess.run(
        ["git", "-C", str(root), "ls-files", "--error-unmatch", path],
        capture_output=True,
        text=True,
        check=False,
    )
    return out.returncode == 0


def check_identity(name: str, email: str) -> Result:
    if name == REQUIRED_NAME and email == REQUIRED_EMAIL:
        return Result(OK, "git identity", f"{name} <{email}>")
    return Result(
        FAIL,
        "git identity",
        f"commits would be authored as {name or '(unset)'} <{email or '(unset)'}>. Run: "
        f'git config user.name "{REQUIRED_NAME}" && git config user.email "{REQUIRED_EMAIL}"',
    )


def check_keys(env: Mapping[str, str]) -> list[Result]:
    out: list[Result] = []
    key = env.get("MODEL_API_KEY", "")
    if not key:
        out.append(
            Result(
                WARN,
                "MODEL_API_KEY",
                "not set (needed only for --live calls). "
                "Set it with: read -s MODEL_API_KEY && export MODEL_API_KEY",
            )
        )
    elif key.startswith("LLM_"):
        out.append(
            Result(OK, "MODEL_API_KEY", "set (shape looks like a Meta key; value not shown)")
        )
    else:
        out.append(
            Result(
                WARN,
                "MODEL_API_KEY",
                "set, but it doesn't start with LLM_ like Meta keys do (value not shown)",
            )
        )
    if env.get("META_API_KEY"):
        out.append(
            Result(
                WARN,
                "META_API_KEY",
                "set in this shell: Muse Code will use it and bill "
                "pay-as-you-go to that account, bypassing a subscription",
            )
        )
    if env.get("CI", "").strip().lower() not in ("", "0", "false", "no"):
        out.append(Result(WARN, "CI", "CI is set: ForgeEmber refuses live calls in this shell"))
    return out


def run_checks(
    cwd: Path,
    env: Mapping[str, str],
    which: Callable[[str], str | None] = shutil.which,
    git_config: Callable[[Path, str], str] = _git_config,
    git_tracked: Callable[[Path, str], bool] = _git_tracked,
) -> list[Result]:
    results: list[Result] = []
    system = platform.system()
    wsl = "microsoft" in platform.release().lower()
    results.append(
        Result(
            OK if system == "Linux" else WARN,
            "platform",
            f"{system}{' (WSL)' if wsl else ''}; ForgeEmber is developed on Linux/WSL",
        )
    )
    for tool, hint in (
        ("git", "sudo apt install -y git"),
        ("uv", "curl -LsSf https://astral.sh/uv/install.sh | sh"),
    ):
        results.append(
            Result(OK, tool, which(tool))
            if which(tool)
            else Result(FAIL, tool, f"not found. Install: {hint}")
        )

    root = find_root(cwd.resolve())
    if root is None:
        results.append(Result(FAIL, "repository", "run this from inside the ForgeEmber clone"))
        return results
    results.append(Result(OK, "repository", str(root)))
    if str(root).startswith("/mnt/"):
        results.append(
            Result(
                WARN,
                "location",
                "the clone is on the Windows drive (/mnt/...): slow "
                "and permission-prone. Clone into your Linux home, e.g. ~/code",
            )
        )

    if which("git"):
        results.append(
            check_identity(git_config(root, "user.name"), git_config(root, "user.email"))
        )
        for secret in (".env", "var/ledger.sqlite"):
            if git_tracked(root, secret):
                results.append(
                    Result(FAIL, "git", f"{secret} is tracked by git: remove it from the index")
                )

    venv_python = root / ".venv" / "bin" / "python"
    if not venv_python.exists():
        results.append(Result(WARN, "virtualenv", "missing: run `uv sync`"))
    else:
        probe = subprocess.run(
            [
                str(venv_python),
                "-c",
                "import sys, forgeember; print('%d.%d' % sys.version_info[:2])",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        version = probe.stdout.strip()
        if probe.returncode != 0:
            results.append(
                Result(FAIL, "virtualenv", "forgeember is not importable: run `uv sync`")
            )
        elif version != "3.12":
            results.append(
                Result(
                    FAIL,
                    "virtualenv",
                    f"Python {version}; the project pins 3.12. Run: rm -rf .venv && uv sync",
                )
            )
        else:
            results.append(Result(OK, "virtualenv", f"Python {version}, forgeember importable"))

    results.extend(check_keys(env))
    if not (root / "fixtures" / "manifest.json").exists():
        results.append(
            Result(
                WARN,
                "fixtures",
                "fixtures/manifest.json missing (arrives with the "
                "Phase 1 PR, or run: uv run python scripts/gen_fixtures.py)",
            )
        )
    return results


def main() -> int:
    results = run_checks(Path.cwd(), os.environ)
    width = max(len(r.check) for r in results)
    for r in results:
        print(f"[{r.status:>4}] {r.check:<{width}}  {r.detail}")
    failed = [r for r in results if r.status == FAIL]
    print(
        f"\n{len(failed)} problem(s) to fix" if failed else "\nready (live calls still need --live)"
    )
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
