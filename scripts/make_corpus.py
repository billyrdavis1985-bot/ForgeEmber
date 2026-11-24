"""Build the scale-run corpora under data/ (gitignored). No API calls; nothing costs money.

    uv run python scripts/make_corpus.py benign --n 3000
    uv run python scripts/make_corpus.py nazario          # 2015-2025, about 3,800 messages
    uv run python scripts/make_corpus.py cord             # 1,000 receipt photos, ~2.3 GB download

benign:  fictional Ashgrove Bakery mail, generated from a seed      -> data/benign-v1/
nazario: Jose Nazario's phishing corpus, CC BY 4.0 (docs/DATA.md),
         downloaded from https://monkey.org/~jose/phishing/          -> data/nazario/
cord:    CORD v2 receipt photos with labeled totals, CC BY 4.0 (docs/DATA.md), from the
         authors' Hugging Face parquet shards, each checked against a pinned sha256 -> data/cord/
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import urllib.request
from pathlib import Path

from forgeember.config import Paths, find_root
from forgeember.corpus import generate_benign, split_mbox, write_cord_split

NAZARIO_BASE = "https://monkey.org/~jose/phishing/"
CORD_BASE = "https://huggingface.co/datasets/naver-clova-ix/cord-v2/resolve/main/data/"
# Shard names and sha256 (the Hugging Face LFS object ids), pinned 2026-11-24.
# 100 test + 100 validation + 800 train = 1,000 receipts.
CORD_SHARDS = {
    "test": [
        (
            "test-00000-of-00001-9c204eb3f4e11791.parquet",
            "51c65f1788faff392abe2a0b55b023eb23e9be551c509138eaa3a832514224e7",
        )
    ],
    "validation": [
        (
            "validation-00000-of-00001-cc3c5779fe22e8ca.parquet",
            "0d0f6dac11fdcc549de2746aa9f53136a3bc22a2a1aff2b0b847f7622ad60c15",
        )
    ],
    "train": [
        (
            "train-00000-of-00004-b4aaeceff1d90ecb.parquet",
            "da3994eee1bf9bd3c57f0d53a72c3a6812c8696c5ba26245987949ddf73483cc",
        ),
        (
            "train-00001-of-00004-7dbbe248962764c5.parquet",
            "cce4def16a0d6a6c75f80be712f7494c56c318a8829b712f5c62650155c9e58e",
        ),
        (
            "train-00002-of-00004-688fe1305a55e5cc.parquet",
            "591e2db8fe8b1d364b054f46e8c375b7f00e72578914ba46a573b6858162cab2",
        ),
        (
            "train-00003-of-00004-2d0cd200555ed7fd.parquet",
            "1ffd9de8d6fbcee7630fd4cdfedff05b9b7fabc0fae4fc17557c5fe7cf178748",
        ),
    ],
}


def _download(url: str, dest: Path) -> None:
    """Download to ``dest`` via a ``.part`` file, so an interrupted download is never reused."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        print(f"have {dest.name}")
        return
    print(f"downloading {url}")
    part = dest.with_name(dest.name + ".part")
    req = urllib.request.Request(url, headers={"User-Agent": "forgeember-corpus/1.0"})
    with urllib.request.urlopen(req, timeout=120) as resp, part.open("wb") as fh:
        shutil.copyfileobj(resp, fh, length=1 << 20)
    part.replace(dest)


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _cord(data: Path, splits: list[str], keep_raw: bool) -> int:
    out = data / "cord"
    raw = out / "raw"
    out.mkdir(parents=True, exist_ok=True)
    for old in out.glob("cord-*.jpg"):  # rebuild from scratch: numbering stays stable
        old.unlink()
    lines: list[dict] = []
    for split in splits:
        for name, sha in CORD_SHARDS[split]:
            shard = raw / name
            _download(CORD_BASE + name, shard)
            got = _sha256(shard)
            if got != sha:
                shard.unlink()
                raise SystemExit(f"{name}: sha256 {got} does not match the pinned {sha}")
            written = write_cord_split(shard, out, split=split)
            lines += written
            print(f"{split}: {name}: {len(written)} receipts")
            if not keep_raw:
                shard.unlink()
    (out / "labels.jsonl").write_text(
        "".join(json.dumps(x) + "\n" for x in lines), encoding="utf-8"
    )
    with_total = sum("total" in x["expected"] for x in lines)
    print(
        f"wrote {len(lines)} receipts ({with_total} with a labeled total) -> {out}\n"
        "attribution: Park et al., CORD (2019), https://github.com/clovaai/cord, CC BY 4.0"
    )
    return 0


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
    c = sub.add_parser("cord")
    c.add_argument("--splits", nargs="+", default=list(CORD_SHARDS), choices=list(CORD_SHARDS))
    c.add_argument("--keep-raw", action="store_true", help="keep the parquet shards (~2.3 GB)")
    args = parser.parse_args(argv)
    data = Paths(root=find_root()).root / "data"

    if args.cmd == "benign":
        labels = generate_benign(data / "benign-v1", args.n)
        print(f"wrote {args.n} benign emails -> {labels.parent}")
        return 0
    if args.cmd == "cord":
        return _cord(data, args.splits, args.keep_raw)

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
