"""Document-set, manifest and packaging checks."""

from __future__ import annotations

from pathlib import Path
import re

import yaml

from jcheng_applications import __version__
from jcheng_applications.model import (
    CONTRACT_HASH,
    INTERFACES_RANGE,
    SYSTEM_API_RANGE,
    load_recipe,
)

ROOT = Path(__file__).resolve().parents[1]

REQUIRED = (
    "README.md",
    "README.zh-CN.md",
    "ROADMAP.md",
    "CHANGELOG.md",
    "ACCEPTANCE.md",
    "LICENSE-REVIEW.md",
    "VERSION",
    ".gitignore",
    "pyproject.toml",
    "capability.yaml",
    "docs/ARCHITECTURE.md",
    "docs/INTEGRATION.md",
    "docs/RECIPES.md",
    "recipes/example-supervised-transfer.yaml",
    "src/jcheng_applications/__init__.py",
    "src/jcheng_applications/model.py",
    "src/jcheng_applications/gateway.py",
    "src/jcheng_applications/runner.py",
    "src/jcheng_applications/cli.py",
    "tests/test_contract.py",
    "tests/test_no_hardware.py",
    "tests/test_recipe.py",
    "tests/test_gateway.py",
    "tests/test_runner.py",
    "tests/test_cli.py",
    "scripts/check_repo_gate.sh",
    "evidence/20261126_scaffold/README.md",
    ".github/CODEOWNERS",
    ".github/workflows/ci.yml",
)


def test_required_files_exist() -> None:
    missing = [name for name in REQUIRED if not (ROOT / name).is_file()]
    assert not missing, f"missing: {missing}"


def test_version_matches_everywhere() -> None:
    version = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
    manifest = yaml.safe_load(
        (ROOT / "capability.yaml").read_text(encoding="utf-8")
    )
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    match = re.search(r'^version = "([^"]+)"', pyproject, re.MULTILINE)
    assert match, "pyproject version not found"
    assert version == __version__ == manifest["version"] == match.group(1) == "0.1.0"


def test_manifest_matches_m3_admission() -> None:
    manifest = yaml.safe_load(
        (ROOT / "capability.yaml").read_text(encoding="utf-8")
    )
    assert set(manifest) == {
        "schema_version",
        "name",
        "version",
        "contract",
        "safety",
        "hardware_access",
        "runtime",
    }
    assert manifest["schema_version"] == 1
    assert manifest["name"] == "jcheng-applications"
    assert manifest["contract"] == {
        "interfaces": INTERFACES_RANGE,
        "contract_hash": CONTRACT_HASH,
        "system_api": SYSTEM_API_RANGE,
    }
    assert manifest["safety"] == {
        "class": "motion_source",
        "scopes": ["arm", "gripper"],
        "requires_lease": True,
        "max_rate_hz": 1,
        "deadman_ms": 500,
    }
    assert manifest["hardware_access"] == "none"
    # A gateway intent source, not a DDS capability.
    assert manifest["runtime"] == {
        "ros_domains": [],
        "publishes": [],
        "subscribes": [],
    }


def test_no_license_before_review() -> None:
    assert not (ROOT / "LICENSE").exists()
    text = (ROOT / "LICENSE-REVIEW.md").read_text(encoding="utf-8")
    assert "Status: **pending**" in text


def test_readme_required_sections() -> None:
    text = (ROOT / "README.md").read_text(encoding="utf-8")
    assert text.startswith("# jcheng-applications")
    assert "> Status: **Experimental**" in text
    for heading in (
        "## Scope and boundary",
        "## Repository layout",
        "## Requirements",
        "## Quick start",
        "## Validation status",
        "## Related repositories",
        "## License",
    ):
        assert heading in text, f"missing section {heading}"


def test_chinese_readme_is_a_translation_pointer() -> None:
    text = (ROOT / "README.zh-CN.md").read_text(encoding="utf-8")
    assert "本文为英文主文档的中文对照" in text
    assert "README.md" in text


def test_status_vocabulary() -> None:
    for name in ("README.md", "ROADMAP.md", "ACCEPTANCE.md"):
        text = (ROOT / name).read_text(encoding="utf-8")
        assert not re.search(r"\b(Done|Finished|WIP|TODO|TBD)\b", text), name


def test_changelog_has_the_release() -> None:
    text = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    assert text.startswith("# Changelog")
    assert "## [Unreleased]" in text
    assert "## [0.1.0] — 2026-11-26" in text


def test_ci_runs_the_gate() -> None:
    text = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    assert "applications-gate" in text
    assert "bash scripts/check_repo_gate.sh" in text


def test_shipped_example_recipe_matches_the_pin() -> None:
    recipe = load_recipe(ROOT / "recipes" / "example-supervised-transfer.yaml")
    assert recipe.contract_hash == CONTRACT_HASH
    assert recipe.scopes == ("arm", "gripper")
