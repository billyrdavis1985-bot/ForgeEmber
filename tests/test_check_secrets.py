"""scripts/check_secrets.py: finds credentials in files to be submitted, never prints them."""

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("check_secrets", ROOT / "scripts/check_secrets.py")
check_secrets = importlib.util.module_from_spec(spec)
sys.modules["check_secrets"] = check_secrets  # dataclasses resolve their module through this
spec.loader.exec_module(check_secrets)

# Assembled at runtime so this file never holds a token-shaped string.
FAKE_META = "LLM_" + "1234567" + "_" + "x9" * 12
FAKE_GITHUB = "gh" + "p_" + "A1b2C3d4" * 5


def test_finds_and_masks_credentials(tmp_path, capsys):
    f = tmp_path / "trajectory.json"
    f.write_text(
        '{"tool": "bash", "output": "ok"}\n'
        f'{{"output": "export MODEL_API_KEY={FAKE_META}"}}\n'
        f"remote: https://{FAKE_GITHUB}@github.com/x/y\n"
        "-----BEGIN OPENSSH PRIVATE KEY-----\n"
    )
    assert check_secrets.main([str(f)]) == 1
    out = capsys.readouterr()
    lines = out.out.splitlines()
    assert {line.split(": ")[1] for line in lines} >= {
        "github-token",
        "api-key-assignment",
        "private-key",
    }
    assert any(line.startswith(f"{f}:2: ledger:") for line in lines)  # the Meta key pattern
    assert FAKE_META not in out.out + out.err and FAKE_GITHUB not in out.out + out.err
    assert "LLM_****" in out.out


def test_clean_files_and_variable_references_pass(tmp_path, capsys):
    f = tmp_path / "notes.md"
    f.write_text(
        "read -s MODEL_API_KEY && export MODEL_API_KEY\n"
        "curl -H 'Content-Type: application/json' https://api.example/v1\n"
        "MODEL_API_KEY=$MODEL_API_KEY uv run forgeember screen x --live\n"
    )
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "a.jsonl").write_text('{"cost_usd": 0.0002}\n')
    assert check_secrets.main([str(f), str(tmp_path / "sub")]) == 0
    assert "clean" in capsys.readouterr().out


def test_missing_path_is_an_error(tmp_path):
    assert check_secrets.main([str(tmp_path / "nope.json")]) == 2
