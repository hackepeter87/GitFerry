from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).parents[2]
REPORT = ROOT / "docs" / "verification-0.1.0-foundation.md"


def test_foundation_report_binds_exact_integrated_and_delivery_evidence() -> None:
    text = REPORT.read_text(encoding="utf-8")

    for value in (
        "dedc0778677f1e88eaf1c0baebfee7a1ead0966d",
        "168cadc28456be2d9faf8111335eccde9002879e",
        "44805207547",
        "44805706378",
        "44808494083",
        "44808991767",
        "27751371",
        "14664143681",
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


def test_release_and_roadmap_link_the_foundation_report() -> None:
    report_link = "verification-0.1.0-foundation.md"

    assert report_link in (ROOT / "docs" / "release.md").read_text(encoding="utf-8")
    assert report_link in (ROOT / "docs" / "roadmap.md").read_text(encoding="utf-8")
