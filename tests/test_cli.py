import json
from pathlib import Path

from forgeember.cli import main
from forgeember.config import Paths, find_root

ROOT_ARGS: list[str] = []


def run(tmp_path, *args):
    return main(["--root", str(tmp_path), *args])


def test_budget_report_on_empty_ledger(tmp_path, capsys):
    assert run(tmp_path, "budget") == 0
    out = capsys.readouterr().out
    assert "ForgeEmber budget" in out
    assert "$0.00" in out and "$9.00" in out
    assert (tmp_path / "var" / "ledger.sqlite").exists()


def test_budget_json(tmp_path, capsys):
    assert run(tmp_path, "budget", "--json") == 0
    snap = json.loads(capsys.readouterr().out)
    assert snap["spent_nusd"] == 0
    assert snap["cap_nusd"] == 9_000_000_000


def test_reconcile_ok_and_undercount(tmp_path, capsys):
    assert run(tmp_path, "budget", "reconcile", "--meta-usd", "0") == 0
    assert "OK" in capsys.readouterr().out
    assert run(tmp_path, "budget", "reconcile", "--meta-usd", "0.01", "--note", "usage page") == 2
    assert "BELOW" in capsys.readouterr().err


def test_transfer_and_expire(tmp_path, capsys):
    args = ["budget", "transfer", "--from-stage", "reserve", "--to-stage", "scale", "--usd", "0.25"]
    assert run(tmp_path, *args, "--approved-by", "Billy Davis", "--reason", "Gate B") == 0
    assert "Moved $0.25 from reserve to scale" in capsys.readouterr().out
    assert run(tmp_path, *args, "--approved-by", " ", "--reason", "x") == 1
    assert run(tmp_path, "budget", "expire-open") == 0
    assert "Closed 0" in capsys.readouterr().out


def test_publish_check(tmp_path, capsys):
    paths = Paths(root=tmp_path)
    paths.readme.write_text("<!-- budget:start -->\n<!-- budget:end -->\n")
    assert run(tmp_path, "budget", "publish", "--check") == 1
    assert run(tmp_path, "budget", "publish") == 0
    assert run(tmp_path, "budget", "publish", "--check") == 0


def test_find_root_locates_repo():
    here = Path(__file__).resolve()
    assert (find_root(here.parent) / "pyproject.toml").exists()
