"""Narrow issue adapter over the existing authenticated Gitea HTTP client."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from gitferry.gitea_provisioning import GiteaProvisioningClient, _repository_segments
from gitferry.operations import OperationBlocked


class GiteaIssueClient(GiteaProvisioningClient):
    def label_ids(self, repository: str, names: list[str]) -> list[int]:
        if not names:
            return []
        owner, name = _repository_segments(repository)
        labels: dict[str, int] = {}
        for page in range(1, 1001):
            values = self._request(
                "GET", f"/repos/{owner}/{name}/labels", params={"page": page, "limit": 50}
            ).json()
            if not isinstance(values, list):
                raise OperationBlocked("target label inventory is invalid")
            for value in values:
                if (
                    not isinstance(value, dict)
                    or not isinstance(value.get("name"), str)
                    or type(value.get("id")) is not int
                ):
                    raise OperationBlocked("target label inventory is invalid")
                if value["name"] in labels:
                    raise OperationBlocked("target label names are ambiguous")
                labels[value["name"]] = value["id"]
            if len(values) < 50:
                break
        else:
            raise OperationBlocked("target label inventory exceeds the supported bound")
        if any(name not in labels for name in names):
            raise OperationBlocked("selected labels require target label provisioning")
        return [labels[name] for name in names]

    def get_issue(self, repository: str, issue_index: int) -> dict[str, Any]:
        owner, name = _repository_segments(repository)
        return self._object("GET", f"/repos/{owner}/{name}/issues/{int(issue_index)}")

    def create_issue(self, repository: str, values: Mapping[str, Any]) -> dict[str, Any]:
        owner, name = _repository_segments(repository)
        return self._object(
            "POST",
            f"/repos/{owner}/{name}/issues",
            json={
                "title": values["title"],
                "body": values["body"],
                "closed": values["state"] == "closed",
                "labels": self.label_ids(repository, values["labels"]),
            },
        )

    def update_issue(
        self, repository: str, issue_index: int, values: Mapping[str, Any]
    ) -> dict[str, Any]:
        # Label mutations use a separate endpoint and journal step; do not silently
        # invent one inside an issue PATCH. Existing unchanged labels are supported.
        current = self.get_issue(repository, issue_index)
        actual = sorted(label["name"] for label in current["labels"])
        if actual != sorted(values["labels"]):
            raise OperationBlocked("issue label changes require a separate supported operation")
        owner, name = _repository_segments(repository)
        return self._object(
            "PATCH",
            f"/repos/{owner}/{name}/issues/{int(issue_index)}",
            json={
                "title": values["title"],
                "body": values["body"],
                "state": values["state"],
            },
        )
