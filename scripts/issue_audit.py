#!/usr/bin/env python3
"""Audit implementation-issue contracts from a JSON snapshot.

The input is intentionally a plain, credential-free snapshot so audits can run in CI or
offline.  It may be read from a file, stdin, or (optionally) fetched with ``gh`` using a
read-only issue query.  No token, URL, or response body is printed by this tool.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

REQUIRED_SECTIONS = (
    "Problem and expected result",
    "Scope",
    "Exclusions",
    "Acceptance criteria",
    "Required tests and evidence",
    "Documentation and compatibility impact",
    "Dependencies and blockers",
    "Failure and recovery",
    "Coherent execution boundary",
)
REQUIRED_BODY_METADATA = ("Depends on", "Blocks", "Target release", "Work kind")
REQUIRED_LABEL_KINDS = ("type", "area", "priority", "risk")
REQUIRED_PROJECT_FIELDS = (
    "Status",
    "Priority",
    "Type",
    "Area",
    "Target release",
    "Risk",
    "Needs Decision",
)
VALID_STATUS = {"Backlog", "Ready", "In Progress", "In Review", "Done"}
EVIDENCE_RE = re.compile(r"Evidence required:\s*(.+)", re.I)
HEADING = re.compile(r"^\s{0,3}#{1,6}\s+(.+?)\s*#*\s*$", re.MULTILINE)


def _text(value: Any) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, dict):
        return str(value.get("name") or value.get("title") or value.get("value") or "").strip()
    return str(value or "").strip()


def _names(values: Any) -> list[str]:
    if not isinstance(values, list):
        return []
    return [_text(value) for value in values if _text(value)]


def _body_sections(body: str) -> dict[str, str]:
    matches = list(HEADING.finditer(body))
    sections: dict[str, str] = {}
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(body)
        sections[match.group(1).strip()] = body[match.end() : end].strip()
    return sections


def _field_names(project_fields: Any) -> dict[str, str]:
    if isinstance(project_fields, dict):
        return {str(key): _text(value) for key, value in project_fields.items()}
    result: dict[str, str] = {}
    if isinstance(project_fields, list):
        for field in project_fields:
            if isinstance(field, dict):
                name = _text(field.get("name") or field.get("field"))
                value = _text(field.get("value") or field.get("option"))
                if name:
                    result[name] = value
    return result


def _normal(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", value.lower()).replace("bug", "bugfix")


def audit_snapshot(snapshot: dict[str, Any]) -> dict[str, Any]:
    """Return stable, machine-readable findings; ``passed`` means no findings."""
    issue = snapshot.get("issue") if isinstance(snapshot.get("issue"), dict) else snapshot
    body = _text(issue.get("body"))
    sections = _body_sections(body)
    labels = _names(issue.get("labels"))
    milestone = _text(issue.get("milestone"))
    project = _field_names(snapshot.get("project", issue.get("project")))
    project_type = project.get("Type") or project.get("Work Type", "")
    missing_sections = [name for name in REQUIRED_SECTIONS if not sections.get(name)]
    missing_metadata = [
        name
        for name in REQUIRED_BODY_METADATA
        if not sections.get(name)
        and not re.search(rf"^\s*{re.escape(name)}\s*:", body, re.MULTILINE | re.IGNORECASE)
    ]
    label_values = {
        kind: [
            label.split(":", 1)[1].strip()
            for label in labels
            if label.lower().startswith(f"{kind}:") and label.split(":", 1)[1].strip()
        ]
        for kind in REQUIRED_LABEL_KINDS
    }
    missing_labels = [kind for kind, values in label_values.items() if len(values) != 1]
    missing_project = [
        name
        for name in REQUIRED_PROJECT_FIELDS
        if not (project.get(name) or (name == "Type" and project_type))
    ]
    evidence = sections.get("Required tests and evidence", "")
    evidence_flags = {
        "has_test_marker": bool(re.search(r"\b(test|pytest|check|verification)\b", evidence, re.I)),
        "has_evidence_marker": bool(
            re.search(r"\b(evidence|probe|artifact|result)\b", evidence, re.I)
        ),
    }
    evidence_match = EVIDENCE_RE.search(evidence)
    if not evidence_match:
        findings_evidence = "missing Evidence required declaration"
    else:
        findings_evidence = ""
    required_evidence = (
        []
        if not evidence_match or evidence_match.group(1).strip().lower() == "none"
        else [item.strip().lower() for item in evidence_match.group(1).split(",") if item.strip()]
    )
    evidence_labels = {label.split(":", 1)[1] for label in labels if label.startswith("evidence:")}
    findings: list[str] = []
    if findings_evidence:
        findings.append(findings_evidence)
    if not milestone:
        findings.append("missing issue milestone")
    milestone_release = milestone.split()[0] if milestone else ""
    body_release = sections.get("Target release", "").strip()
    if not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", milestone_release):
        findings.append("milestone lacks an exact release version")
    if milestone_release != project.get("Target release"):
        findings.append("milestone does not match project target release")
    if milestone_release != body_release:
        findings.append("milestone does not match body target release")
    state = _text(issue.get("state")).lower()
    if state not in {"open", "closed"}:
        findings.append("missing or invalid issue state")
    elif (state == "closed") != (project.get("Status") == "Done"):
        findings.append("issue state contradicts project status")
    if project.get("Status") not in VALID_STATUS:
        findings.append("invalid project status")
    if project.get("Needs Decision") not in {"Yes", "No"}:
        findings.append("Needs Decision must be Yes or No")
    if (
        project_type
        and sections.get("Work kind")
        and project_type.lower() != sections["Work kind"].strip().lower()
    ):
        findings.append("project work type does not match body work kind")
    for kind, field in (
        ("type", project_type),
        ("area", project.get("Area", "")),
        ("priority", project.get("Priority", "")),
        ("risk", project.get("Risk", "")),
    ):
        if field and label_values.get(kind) and _normal(label_values[kind][0]) != _normal(field):
            findings.append(f"{kind} label does not match project field")
    for metadata in ("Depends on", "Blocks"):
        match = re.search(rf"^[ \t]*{metadata}[ \t]*:[ \t]*(.*)$", body, re.I | re.M)
        if not match or not match.group(1).strip():
            findings.append(f"{metadata} must have a value or None")
    if set(required_evidence) - {
        "browser",
        "recovery",
        "directory",
        "capacity",
        "immutable-artifact",
    }:
        findings.append("unknown evidence requirement")
    if evidence_match and evidence_labels != set(required_evidence):
        findings.append("evidence labels do not match Evidence required")
    findings.extend(f"missing section: {name}" for name in missing_sections)
    findings.extend(f"missing body metadata: {name}" for name in missing_metadata)
    findings.extend(f"missing label kind: {name}" for name in missing_labels)
    findings.extend(f"missing project field: {name}" for name in missing_project)
    if not evidence_flags["has_test_marker"]:
        findings.append("evidence section lacks a test/check marker")
    if not evidence_flags["has_evidence_marker"]:
        findings.append("evidence section lacks an evidence/result marker")
    return {
        "status": "passed" if not findings else "failed",
        "issue": issue.get("number") or issue.get("id"),
        "findings": findings,
        "missing_sections": missing_sections,
        "missing_milestone": not bool(milestone),
        "missing_body_metadata": missing_metadata,
        "missing_label_kinds": missing_labels,
        "missing_project_fields": missing_project,
        "evidence_flags": evidence_flags,
        "required_evidence": required_evidence,
    }


def github_snapshots(issues: list[Any], project: dict[str, Any]) -> list[dict[str, Any]]:
    """Normalize complete read-only GitHub exports; no network or local authority changes."""
    if issues and isinstance(issues[0], list):
        issues = [issue for page in issues for issue in page]
    items = project.get("items", [])
    if project.get("totalCount") != len(items):
        raise ValueError("incomplete Project export")
    indexed = {
        item.get("content", {}).get("number"): item
        for item in items
        if item.get("content", {}).get("repository") == "hackepeter87/GitFerry"
    }
    fields = {
        "Status": "status",
        "Priority": "priority",
        "Type": "work Type",
        "Area": "area",
        "Target release": "target Release",
        "Risk": "risk",
        "Needs Decision": "needs Decision",
    }
    snapshots = []
    for issue in issues:
        if "pull_request" in issue:
            continue
        number = issue.get("number", 0)
        if not (
            8 <= number <= 36
            or number in (47, 48)
            or (issue.get("state", "").lower() == "open" and issue.get("milestone"))
        ):
            continue
        item = indexed.get(number, {})
        snapshots.append(
            {"issue": issue, "project": {name: item.get(key, "") for name, key in fields.items()}}
        )
    if not snapshots:
        raise ValueError("empty issue scope")
    return snapshots


def _load(args: argparse.Namespace) -> list[dict[str, Any]]:
    if args.github_issues or args.github_project:
        if args.snapshot or not (args.github_issues and args.github_project):
            raise ValueError("provide both GitHub exports, or one normalized snapshot")
        return github_snapshots(
            json.loads(Path(args.github_issues).read_text()),
            json.loads(Path(args.github_project).read_text()),
        )
    raw = Path(args.snapshot).read_text(encoding="utf-8") if args.snapshot else sys.stdin.read()
    parsed = json.loads(raw)
    if isinstance(parsed, dict):
        return [parsed]
    if isinstance(parsed, list) and parsed and all(isinstance(item, dict) for item in parsed):
        return parsed
    raise ValueError("snapshot must be a nonempty object or list of objects")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("snapshot", nargs="?", help="JSON snapshot path; defaults to stdin")
    parser.add_argument(
        "--github-issues", help="complete gh api issues export (--paginate --slurp)"
    )
    parser.add_argument("--github-project", help="complete gh project item-list JSON export")
    args = parser.parse_args(argv)
    try:
        results = [audit_snapshot(snapshot) for snapshot in _load(args)]
        result = {
            "status": "passed" if all(item["status"] == "passed" for item in results) else "failed",
            "results": results,
        }
    except (OSError, ValueError, TypeError, AttributeError, KeyError):
        print(json.dumps({"status": "failed", "findings": ["unable to load complete snapshot"]}))
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0 if result["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
