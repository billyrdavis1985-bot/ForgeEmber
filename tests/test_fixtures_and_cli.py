"""Fixtures are reproducible and labeled; the screen CLI never spends without --live."""

import json
from pathlib import Path

from forgeember.cli import main
from forgeember.fixturegen import generate

ROOT = Path(__file__).resolve().parents[1]


def test_fixtures_regenerate_byte_for_byte(tmp_path):
    generate(tmp_path, images=False, audio=False)
    for path in tmp_path.rglob("*"):
        if path.is_file():
            committed = ROOT / "fixtures" / path.relative_to(tmp_path)
            assert committed.read_bytes() == path.read_bytes(), (
                f"{committed} is stale: run gen_fixtures"
            )


def test_manifest_labels_are_complete():
    items = json.loads((ROOT / "fixtures" / "manifest.json").read_text())["items"]
    assert len({i["id"] for i in items}) == len(items)
    for item in items:
        assert (ROOT / "fixtures" / item["file"]).exists()
        assert item["expected"]["risk"] in ("low", "medium", "high")
        assert "pay" not in item["expected"]["held"] or item["expected"]["category"] in (
            "invoice",
            "payment_request",
            "executive_request",
        )
    assert {i["attack"] for i in items} >= {
        "bank_detail_change",
        "executive_impersonation",
        "prompt_injection",
        "reply_to_diversion",
    }


def test_screen_refuses_without_live(tmp_path, capsys):
    (tmp_path / "pyproject.toml").write_text('name = "forgeember"\n')
    code = main(
        [
            "--root",
            str(tmp_path),
            "screen",
            str(ROOT / "fixtures/emails/em-004-newsletter.eml"),
            "--stage",
            "dev",
            "--baselines",
            str(ROOT / "fixtures/baselines.json"),
        ]
    )
    assert code == 1
    assert "live" in capsys.readouterr().err
    assert not (tmp_path / "var" / "receipts").exists()


def test_holds_cli_empty_and_decide_requires_name(tmp_path, capsys):
    assert main(["--root", str(tmp_path), "holds"]) == 0
    assert "no pending holds" in capsys.readouterr().out
    assert main(["--root", str(tmp_path), "holds", "decide", "1", "--approve", "--by", " "]) == 1
