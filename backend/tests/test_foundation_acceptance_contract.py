from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).parents[2]
REPORT = ROOT / "docs" / "verification-0.1.0-foundation.md"


def test_foundation_report_binds_exact_integrated_and_delivery_evidence() -> None:
    text = REPORT.read_text(encoding="utf-8")

    for value in (
        "dedc0778677f1e88eaf1c0baebfee7a1ead0966d",
        "07fe353a896b83a29ee8013bddb80ef638e50c56",
        "168cadc28456be2d9faf8111335eccde9002879e",
        "648de07afcecf4db2176491ecb47b6dff4bb061c",
        "44805207547",
        "44805706378",
        "44808494083",
        "44808991767",
        "44811003946",
        "44811562483",
        "64bf3dd5bc4ddc06e57bfe2bfd09f8d0fb98a14e",
        "27751371",
        "14664143681",
        "14668890407",
        "7049764078",
        "d20286ca2b2e170fdf628e7231b8a31a3220ade39ff462b55041d43d1fc757dd",
    ):
        assert value in text


def test_foundation_report_keeps_evidence_classes_and_release_boundary_explicit() -> None:
    text = REPORT.read_text(encoding="utf-8")
    normalized = " ".join(text.split())

    for classification in (
        "Locally executed",
        "Hosted executed",
        "Configured-only",
        "Supported-with-limits",
        "Blocked",
        "Not yet tested",
    ):
        assert classification in text
    assert "no 0.1 artifact candidate" in text.lower()
    assert "does not approve a stable release" in normalized
    assert "does not claim 0.2 user-flow acceptance" in normalized
    assert "no duplicate" in text
    assert "fork_turns=none" in text
    assert "unrelated Dependabot PR #38" in text
    assert "create no issue, branch or PR" in text


def test_release_and_roadmap_link_the_foundation_report() -> None:
    report_link = "verification-0.1.0-foundation.md"

    assert report_link in (ROOT / "docs" / "release.md").read_text(encoding="utf-8")
    assert report_link in (ROOT / "docs" / "roadmap.md").read_text(encoding="utf-8")
