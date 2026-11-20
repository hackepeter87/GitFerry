from gitferry.baselines import issue_baseline, issue_has_drift


def issue(**overrides: object) -> dict[str, object]:
    value: dict[str, object] = {
        "id": 10,
        "number": 4,
        "title": "Original title",
        "body": "Original body",
        "state": "open",
        "labels": [{"name": "beta"}, {"name": "alpha"}],
        "created_at": "2026-11-20T10:00:00Z",
        "updated_at": "2026-11-20T10:00:00Z",
    }
    value.update(overrides)
    return value


def test_issue_baseline_covers_only_selected_central_fields() -> None:
    baseline = issue_baseline(issue())

    assert baseline.fields == ("title", "body", "state", "labels")
    assert baseline.values["labels"] == ("alpha", "beta")
    assert "id" not in baseline.values
    assert "created_at" not in baseline.values
    assert "updated_at" not in baseline.values


def test_issue_content_change_is_detected_as_drift() -> None:
    baseline = issue_baseline(issue())

    assert issue_has_drift(baseline, issue(body="Changed centrally")) is True


def test_timestamp_only_change_is_not_claimed_as_selected_content_drift() -> None:
    baseline = issue_baseline(issue())

    assert issue_has_drift(baseline, issue(updated_at="2026-11-20T11:00:00Z")) is False
