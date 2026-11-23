"""Architectural guardrails enforced as tests."""

import ast
from pathlib import Path

from forgeember.policy import Tier, tier_for

SRC = Path(__file__).resolve().parents[1] / "src" / "forgeember"
NETWORK_MODULES = {"httpx", "requests", "urllib", "urllib3", "http", "socket", "aiohttp", "openai"}
ALLOWED_NETWORK_FILE = SRC / "ember" / "client.py"


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            names.add(node.module.split(".")[0])
    return names


def test_only_the_ember_client_imports_network_libraries():
    offenders = {
        str(p.relative_to(SRC)): sorted(_imports(p) & NETWORK_MODULES)
        for p in SRC.rglob("*.py")
        if p != ALLOWED_NETWORK_FILE and _imports(p) & NETWORK_MODULES
    }
    assert offenders == {}


def test_t2_actions_are_held_and_unknown_actions_default_to_t2():
    for action in ("pay", "change_bank_details", "send_external", "delete"):
        assert tier_for(action) is Tier.T2
    assert tier_for("something_new") is Tier.T2
    assert tier_for("file") is Tier.T0


def test_no_secrets_or_env_files_committed():
    root = SRC.parents[1]
    assert not (root / ".env").exists() or ".env" in (root / ".gitignore").read_text()
    example = (root / ".env.example").read_text()
    assert "MODEL_API_KEY=" in example
    assert "LLM_" not in example
