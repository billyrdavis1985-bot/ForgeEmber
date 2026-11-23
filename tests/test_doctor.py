"""scripts/doctor.py: checks identity and key presence without ever printing the key."""

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("doctor", ROOT / "scripts" / "doctor.py")
doctor = importlib.util.module_from_spec(spec)
sys.modules["doctor"] = doctor  # dataclasses resolve their module through sys.modules
spec.loader.exec_module(doctor)

SECRET = "LLM_000000000000_FAKE_TEST_VALUE"


def test_identity_must_match_project_rule():
    assert doctor.check_identity(doctor.REQUIRED_NAME, doctor.REQUIRED_EMAIL).status == doctor.OK
    bad = doctor.check_identity("Claude", "noreply@anthropic.com")
    assert bad.status == doctor.FAIL and "git config user.name" in bad.detail


def test_key_checks_never_reveal_the_value():
    results = doctor.check_keys({"MODEL_API_KEY": SECRET, "META_API_KEY": "x", "CI": "true"})
    statuses = {r.check: r.status for r in results}
    assert statuses == {"MODEL_API_KEY": doctor.OK, "META_API_KEY": doctor.WARN, "CI": doctor.WARN}
    assert all(SECRET not in r.detail and "FAKE" not in r.detail for r in results)
    assert doctor.check_keys({})[0].status == doctor.WARN


def test_run_checks_in_this_repo_with_fakes():
    results = doctor.run_checks(
        ROOT / "src",
        env={},
        which=lambda tool: f"/usr/bin/{tool}",
        git_config=lambda root, key: {
            "user.name": doctor.REQUIRED_NAME,
            "user.email": doctor.REQUIRED_EMAIL,
        }[key],
        git_tracked=lambda root, path: False,
    )
    by_check = {r.check: r for r in results}
    assert by_check["repository"].status == doctor.OK
    assert by_check["git identity"].status == doctor.OK
    assert not any(r.status == doctor.FAIL and r.check in ("uv", "git") for r in results)


def test_missing_tools_fail(tmp_path):
    results = doctor.run_checks(tmp_path, env={}, which=lambda tool: None)
    assert {r.check for r in results if r.status == doctor.FAIL} >= {"uv", "git", "repository"}
