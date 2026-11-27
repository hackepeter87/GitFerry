from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).parents[2]
MATRIX = ROOT / "docs" / "capability-matrix.md"

ALLOWED_STATUSES = {
    "supported",
    "supported-with-limits",
    "not-supported",
    "not-checked",
    "blocked",
    "not-implemented",
}
TARGET_RELEASES = {"0.2.0", "0.3.0", "0.4.0", "0.5.0", "0.6.0", "1.0.0"}
REQUIRED_IDS = {
    "blueprint-config",
    "oci-runtime",
    "admin-auth",
    "gitea-connections",
    "transfer-plan-execution",
    "content-inventory-plan",
    "git-refs-history",
    "git-lfs",
    "git-wiki",
    "structure-rights",
    "identity-bindings",
    "auth-source-bootstrap",
    "directory-integration",
    "directory-ad",
    "issues",
    "labels",
    "issue-comments",
    "attachments",
    "releases-assets",
    "pull-requests",
    "reviews-review-comments",
    "timelines-reactions",
    "origins-replicas",
    "authors",
    "historical-timestamps",
    "drift-conflicts",
    "restart-unknown-outcome",
    "offline-isolation",
    "manifest-integrity",
    "package-storage",
    "application-package-files",
    "backup-restore-upgrade",
    "ui-first-import",
    "ui-return-conflicts",
    "load-30gb",
    "install-operations-docs",
    "browser-acceptance",
    "release-candidate",
}
EXPLICIT_GAP_OWNERS = {
    "transfer-plan-execution": ("0.2.0", "9"),
    "application-package-files": ("0.2.0", "10"),
    "auth-source-bootstrap": ("0.3.0", "13"),
    "content-inventory-plan": ("0.4.0", "17"),
}
OWNER_RE = re.compile(
    r"^\[#(?P<number>\d+)\]"
    r"\(https://github\.com/hackepeter87/GitFerry/issues/(?P=number)\)$"
)
LOCAL_LINK_RE = re.compile(r"\[[^]]+\]\((?!https?://)([^)#]+)(?:#[^)]+)?\)")


def matrix_rows() -> list[dict[str, str]]:
    lines = MATRIX.read_text(encoding="utf-8").splitlines()
    header_index = next(
        index for index, line in enumerate(lines) if line.startswith("| ID | Capability |")
    )
    headers = [cell.strip() for cell in lines[header_index].strip("|").split("|")]
    rows: list[dict[str, str]] = []
    for line in lines[header_index + 2 :]:
        if not line.startswith("| `"):
            break
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        assert len(cells) == len(headers), line
        rows.append(dict(zip(headers, cells, strict=False)))
    return rows


def test_matrix_covers_every_agreed_capability_with_complete_status_fields() -> None:
    rows = matrix_rows()
    ids = {row["ID"].strip("`") for row in rows}

    assert ids == REQUIRED_IDS
    assert len(rows) == len(REQUIRED_IDS)
    for row in rows:
        assert row["Product need"]
        assert row["Dependencies"]
        assert row["Evidence"]
        assert row["Limits / decisions"]
        assert row["Implementation"] in ALLOWED_STATUSES
        assert row["First import"] in ALLOWED_STATUSES
        assert row["Return"] in ALLOWED_STATUSES
        assert row["Target release"] in TARGET_RELEASES
        assert OWNER_RE.fullmatch(row["Owner"]), row


def test_matrix_local_evidence_links_resolve() -> None:
    for row in matrix_rows():
        links = LOCAL_LINK_RE.findall(row["Evidence"])
        assert links, row
        for link in links:
            assert (MATRIX.parent / link).resolve().exists(), (row["ID"], link)


def test_matrix_does_not_overstate_label_return_and_names_foundational_gap_owners() -> None:
    rows = {row["ID"].strip("`"): row for row in matrix_rows()}
    labels = rows["labels"]

    assert labels["Implementation"] == "supported-with-limits"
    assert labels["First import"] == "supported-with-limits"
    assert labels["Return"] == "not-checked"
    for capability_id, (release, issue) in EXPLICIT_GAP_OWNERS.items():
        row = rows[capability_id]
        assert row["Target release"] == release
        assert row["Owner"] == (
            f"[#{issue}](https://github.com/hackepeter87/GitFerry/issues/{issue})"
        )


def test_matrix_keeps_known_boundaries_and_integration_baselines_visible() -> None:
    text = MATRIX.read_text(encoding="utf-8")
    normalized = " ".join(text.split())
    compose = (ROOT / "tests" / "integration" / "compose.yaml").read_text(encoding="utf-8")
    historical_digest = "sha256:d20286ca2b2e170fdf628e7231b8a31a3220ade39ff462b55041d43d1fc757dd"
    primary_digest = "sha256:aa90e8f00b755b0d665214ab9c14447ea44d3190dbe6b6d0cb46cd9c4f55d309"

    assert "Gitea 1.27.2" in text
    assert historical_digest in text
    assert "Gitea 28.4.0" in text
    assert primary_digest in text
    assert primary_digest in compose
    assert "no general exactly-once guarantee" in text
    assert "do not restore native `created_at`" in text
    assert "no atomic server-side version precondition" in text
    assert "directory authority remains local" in text
    assert "Current manifests are unsigned" in text
    assert "Manifest v1 carries issues; Manifest v2 adds normal issue comments" in normalized
    assert "SQLite schema versions are independent" in normalized
    assert "native Gitea numeric ID is an instance-scoped replica fact" in normalized
