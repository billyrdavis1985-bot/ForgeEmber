"""Scan files for credentials before they're committed or submitted. Never prints a secret.

    uv run python scripts/check_secrets.py traces/muse-code-trajectory.json
    uv run python scripts/check_secrets.py traces/*.jsonl traces/*.json

Meant for files we didn't write line by line, above all the Muse Code trajectory export: a
coding session's log holds whatever its tools printed. Each finding shows the file, line,
pattern and a masked snippet (first 4 characters, then ****). Exit 1 if anything matches:
review each finding, remove real secrets, and rotate any key that was exposed.
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path

from forgeember.budget.ledger import SECRET_PATTERNS

# The ledger's trace patterns (Meta keys, bearer tokens, auth headers, sk- keys), plus
# credentials a coding session could pick up from its environment.
PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    *((f"ledger:{p.pattern}", p) for p in SECRET_PATTERNS),
    ("github-token", re.compile(r"\b(gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})")),
    ("api-key-assignment", re.compile(r"\b(MODEL|META)_API_KEY\s*[:=]\s*['\"]?(?!\$)[^\s'\"]{8,}")),
    ("aws-access-key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("private-key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
)


@dataclass(frozen=True)
class Finding:
    path: Path
    line: int
    pattern: str
    masked: str

    def __str__(self) -> str:
        return f"{self.path}:{self.line}: {self.pattern}: {self.masked}"


def mask(text: str) -> str:
    return text[:4] + "****"


def scan_text(text: str, path: Path) -> list[Finding]:
    findings = []
    for lineno, line in enumerate(text.splitlines(), start=1):
        for name, pattern in PATTERNS:
            for m in pattern.finditer(line):
                findings.append(Finding(path, lineno, name, mask(m.group(0))))
    return findings


def scan(paths: list[Path]) -> list[Finding]:
    findings: list[Finding] = []
    for path in paths:
        files = sorted(p for p in path.rglob("*") if p.is_file()) if path.is_dir() else [path]
        for f in files:
            findings += scan_text(f.read_text(encoding="utf-8", errors="replace"), f)
    return findings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paths", nargs="+", type=Path, help="files or folders to scan")
    args = parser.parse_args(argv)
    missing = [p for p in args.paths if not p.exists()]
    if missing:
        print(f"error: not found: {', '.join(map(str, missing))}", file=sys.stderr)
        return 2
    findings = scan(args.paths)
    for finding in findings:
        print(finding)
    if findings:
        print(f"{len(findings)} possible secret(s): review before committing", file=sys.stderr)
        return 1
    print(f"clean: {len(args.paths)} path(s) scanned")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
