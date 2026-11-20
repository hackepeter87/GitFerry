"""Selected-object baselines used by bounded roundtrip validation probes."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

ISSUE_BASELINE_FIELDS = ("title", "body", "state", "labels")
COMMENT_BASELINE_FIELDS = ("body",)


class BaselineInputError(ValueError):
    """Raised when untrusted API data cannot form a safe baseline."""


@dataclass(frozen=True)
class IssueBaseline:
    fields: tuple[str, ...]
    values: Mapping[str, object]


@dataclass(frozen=True)
class CommentBaseline:
    fields: tuple[str, ...]
    values: Mapping[str, object]


def _required_text(issue: Mapping[str, Any], field: str) -> str:
    value = issue.get(field)
    if not isinstance(value, str):
        raise BaselineInputError(f"issue field {field!r} must be text")
    return value


def _label_names(issue: Mapping[str, Any]) -> tuple[str, ...]:
    labels = issue.get("labels")
    if not isinstance(labels, Sequence) or isinstance(labels, (str, bytes)):
        raise BaselineInputError("issue field 'labels' must be a list")
    names: list[str] = []
    for label in labels:
        if not isinstance(label, Mapping) or not isinstance(label.get("name"), str):
            raise BaselineInputError("every issue label must contain a text name")
        names.append(label["name"])
    return tuple(sorted(names))


def issue_baseline(issue: Mapping[str, Any]) -> IssueBaseline:
    """Capture only the central issue fields selected by the current probe."""
    return IssueBaseline(
        fields=ISSUE_BASELINE_FIELDS,
        values={
            "title": _required_text(issue, "title"),
            "body": _required_text(issue, "body"),
            "state": _required_text(issue, "state"),
            "labels": _label_names(issue),
        },
    )


def issue_has_drift(baseline: IssueBaseline, current: Mapping[str, Any]) -> bool:
    """Return whether one of the explicitly selected issue fields changed."""
    return baseline.values != issue_baseline(current).values


def comment_baseline(comment: Mapping[str, Any]) -> CommentBaseline:
    """Capture the sole selected field of a normal issue comment."""

    return CommentBaseline(
        fields=COMMENT_BASELINE_FIELDS,
        values={"body": _required_text(comment, "body")},
    )


def comment_has_drift(baseline: CommentBaseline, current: Mapping[str, Any]) -> bool:
    return baseline.values != comment_baseline(current).values
