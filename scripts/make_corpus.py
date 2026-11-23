"""Build the scale-run corpora under data/ (gitignored). No API calls; nothing costs money.

    uv run python scripts/make_corpus.py benign --n 3000
    uv run python scripts/make_corpus.py nazario          # 2015-2025, about 3,800 messages

benign:  fictional Ashgrove Bakery mail, generated from a seed      -> data/benign-v1/
nazario: Jose Nazario's phishing corpus, CC BY 4.0 (docs/DATA.md),
         downloaded from https://monkey.org/~jose/phishing/          -> data/nazario/
"""

from __future__ import annotations

import argparse
import shutil
import urllib.request

from forgeember.config import Paths, find_root
from forgeember.corpus import generate_benign, split_mbox

NAZARIO_BASE = "https://monkey.org/~jose/phishing/"


def _download(url: str, dest) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        print(f"have {dest.name}")
        return
    print(f"downloading {url}")
    req = urllib.request.Request(url, headers={"User-Agent": "forgeember-corpus/1.0"})
    with urllib.request.urlopen(req, timeout=120) as resp, dest.open("wb") as fh:
        shutil.copyfileobj(resp, fh)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    sub = parser.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("benign")
    b.add_argument("--n", type=int, default=3000)
    n = sub.add_parser("nazario")
    n.add_argument("--years", nargs="+", default=[str(y) for y in range(2015, 2026)])
    n.add_argument("--max-bytes", type=int, default=1_000_000, help="skip larger messages")
    args = parser.parse_args(argv)
    data = Paths(root=find_root()).root / "data"

    if args.cmd == "benign":
        labels = generate_benign(data / "benign-v1", args.n)
        print(f"wrote {args.n} benign emails -> {labels.parent}")
        return 0

    root = data / "nazario"
    out = root / "eml"
    if (out / "labels.jsonl").exists():
        (out / "labels.jsonl").unlink()  # rebuild labels from scratch
    _download(NAZARIO_BASE + "LICENSE.txt", root / "LICENSE.txt")
    _download(NAZARIO_BASE + "README.txt", root / "README.txt")
    for year in args.years:
        raw = root / "raw" / f"phishing-{year}"
        _download(f"{NAZARIO_BASE}phishing-{year}", raw)
        r = split_mbox(
            raw,
            out,
            label="phish",
            source=f"nazario:phishing-{year} (CC BY 4.0)",
            prefix=f"nazario-{year}",
            max_bytes=args.max_bytes,
        )
        print(
            f"phishing-{year}: {r.written} messages "
            f"({r.skipped_large} too large, {r.skipped_broken} unreadable)"
        )
    print(f"labels -> {out / 'labels.jsonl'}  (attribution: Jose Nazario, CC BY 4.0)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
