"""Versioned selected-content facts; inventory is never local execution authority."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Any, Literal

from jsonschema import Draft202012Validator, FormatChecker
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from gitferry.gitea_provisioning import ProvisioningTargetConflict, _repository_segments
from gitferry.state_codec import canonical_json

# Coverage is explicit and versioned. New classes must not silently disappear
# because a collector has no endpoint or a transfer adapter is not implemented.
CONTENT_CLASSES = (
    "git_refs",
    "lfs",
    "wiki",
    "organizations",
    "teams",
    "permissions",
    "identities",
    "issues",
    "labels",
    "comments",
    "attachments",
    "releases",
    "release_assets",
    "pull_requests",
    "pull_comments",
    "reviews",
    "review_comments",
    "timelines",
    "reactions",
    "milestones",
    "other_content",
)
TRANSFER_CLASSES = frozenset(
    {"git_refs", "organizations", "teams", "permissions", "identities", "issues", "comments"}
)
COMPARISON_VERSION = 1
NativeId = Annotated[str, Field(min_length=1, max_length=512)]
Nonnegative = Annotated[int, Field(ge=0)]


class InventoryError(ValueError):
    """Inventory facts cannot safely establish the selected scope."""


class _Model(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")


class InventoryObject(_Model):
    key: NativeId
    namespace: Annotated[str, Field(min_length=1, max_length=128)]
    native_id: NativeId | None = None
    issue_index: NativeId | None = None
    parent: dict[str, str] | None = None
    author: dict[str, str] | None = None
    facts: dict[str, Any] = Field(default_factory=dict)
    size_bytes: Nonnegative | None = None

    @model_validator(mode="after")
    def relationships(self) -> InventoryObject:
        if self.parent is not None:
            if (
                set(self.parent) != {"content_class", "key"}
                or self.parent["content_class"] not in CONTENT_CLASSES
                or not self.parent["key"]
            ):
                raise ValueError("invalid parent relationship")
        if self.author is not None:
            if (
                set(self.author) != {"account_id", "login"}
                or not self.author["account_id"].isdigit()
                or int(self.author["account_id"]) < 1
                or not self.author["login"]
            ):
                raise ValueError("invalid native author relationship")
        if self.issue_index is not None and (
            not self.issue_index.isdigit() or int(self.issue_index) < 1
        ):
            raise ValueError("invalid repository issue index")
        return self


class ClassObservation(_Model):
    content_class: str
    status: Literal["observed", "not-supported", "not-checked", "excluded", "blocked", "failed"]
    count: Nonnegative | None
    objects: list[InventoryObject] = Field(default_factory=list, max_length=10000)
    reason: Annotated[str, Field(max_length=1024)] = ""
    limitations: list[str] = Field(default_factory=list, max_length=64)

    @model_validator(mode="after")
    def coherent(self) -> ClassObservation:
        if self.content_class not in CONTENT_CLASSES:
            raise ValueError("unknown inventory class")
        if self.status == "observed":
            if self.count != len(self.objects):
                raise ValueError("observed count must match concrete objects")
        elif self.count is not None or self.objects or not self.reason:
            raise ValueError("unobserved content needs an unknown count and explicit reason")
        identities = [(value.namespace, value.key) for value in self.objects]
        if len(set(identities)) != len(identities):
            raise ValueError("duplicate inventory object identity")
        return self


class RepositoryInventory(_Model):
    repository: Annotated[str, Field(min_length=3, max_length=256)]
    native_id: Annotated[str, Field(pattern=r"^[1-9][0-9]*$", max_length=128)]
    requested_classes: list[str] = Field(min_length=1, max_length=len(CONTENT_CLASSES))
    classes: list[ClassObservation] = Field(
        min_length=len(CONTENT_CLASSES), max_length=len(CONTENT_CLASSES)
    )
    git_size_estimate_bytes: Nonnegative | None = None
    size_assumptions: list[str] = Field(default_factory=list, max_length=32)

    @model_validator(mode="after")
    def complete_catalog(self) -> RepositoryInventory:
        names = [item.content_class for item in self.classes]
        if set(names) != set(CONTENT_CLASSES) or len(names) != len(set(names)):
            raise ValueError("every inventory class must be represented exactly once")
        if len(set(self.requested_classes)) != len(self.requested_classes) or not set(
            self.requested_classes
        ) <= set(CONTENT_CLASSES):
            raise ValueError("requested inventory classes are invalid")
        references = {
            entry.content_class: {item.key for item in entry.objects} for entry in self.classes
        }
        for entry in self.classes:
            for item in entry.objects:
                if (
                    item.parent is not None
                    and item.parent["key"] not in references[item.parent["content_class"]]
                ):
                    raise ValueError("inventory parent reference cannot be resolved")
        return self


class InventoryDocument(_Model):
    format: Literal["gitferry.content-inventory"] = "gitferry.content-inventory"
    schema_version: Literal[1] = 1
    comparison_version: Literal[1] = 1
    snapshot_id: Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")]
    created_at: Annotated[str, Field(min_length=1, max_length=64)]
    source_instance_key: Annotated[str, Field(min_length=1, max_length=128)]
    source_installation_marker: Annotated[str, Field(min_length=1, max_length=256)]
    gitea_version: Literal["1.27.2"]
    repositories: list[RepositoryInventory] = Field(min_length=1, max_length=128)
    deletion_semantics: Literal["absence-does-not-delete"] = "absence-does-not-delete"

    @model_validator(mode="after")
    def unique_scope(self) -> InventoryDocument:
        observed_at = datetime.fromisoformat(self.created_at.replace("Z", "+00:00"))
        if observed_at.tzinfo is None:
            raise ValueError("inventory creation time requires a timezone")
        for repository in self.repositories:
            _repository_segments(repository.repository)
        if len({r.repository for r in self.repositories}) != len(self.repositories) or len(
            {r.native_id for r in self.repositories}
        ) != len(self.repositories):
            raise ValueError("duplicate selected repository identity")
        return self


@lru_cache(maxsize=1)
def _validator() -> Draft202012Validator:
    schema = Path(__file__).resolve().parents[3] / "schemas/v1/content-inventory.schema.json"
    if not schema.is_file():
        schema = Path.cwd() / "schemas/v1/content-inventory.schema.json"
    return Draft202012Validator(json.loads(schema.read_text()), format_checker=FormatChecker())


def parse_inventory(value: dict[str, Any]) -> InventoryDocument:
    try:
        if value.get("format") != "gitferry.content-inventory":
            raise ValueError("inventory format is required")
        if len(json.dumps(value, ensure_ascii=False, allow_nan=False).encode()) > 16 * 1024 * 1024:
            raise ValueError("inventory exceeds the document byte limit")
        pending = [(value, 0)]
        while pending:
            current, depth = pending.pop()
            if depth > 32:
                raise ValueError("inventory nesting exceeds its limit")
            if isinstance(current, dict):
                pending.extend((item, depth + 1) for item in current.values())
            elif isinstance(current, list):
                pending.extend((item, depth + 1) for item in current)
        if (
            type(value.get("schema_version")) is not int
            or type(value.get("comparison_version")) is not int
        ):
            raise ValueError("inventory versions must be integers")
        if next(_validator().iter_errors(value), None) is not None:
            raise ValueError("inventory does not match its supported local schema")
        return InventoryDocument.model_validate(value)
    except (ValueError, TypeError, RecursionError, ValidationError, ProvisioningTargetConflict):
        raise InventoryError("invalid or unsupported content inventory") from None


def inventory_digest(document: InventoryDocument) -> str:
    """Project canonical_json content digest, not a standard/signature/byte digest."""
    return hashlib.sha256(canonical_json(document.model_dump(mode="json")).encode()).hexdigest()


def comparison_facts(document: InventoryDocument) -> dict[str, Any]:
    """Snapshot ID/time are observations; installation and selected facts are baseline."""
    value = document.model_dump(mode="json")
    del value["snapshot_id"]
    del value["created_at"]
    for repository in value["repositories"]:
        repository.pop("git_size_estimate_bytes", None)
        repository.pop("size_assumptions", None)
        repository["classes"] = [
            item
            for item in repository["classes"]
            if item["content_class"] in repository["requested_classes"]
        ]
    return value


def inventory_summary(document: InventoryDocument) -> dict[str, Any]:
    blockers: list[dict[str, str]] = []
    exclusions: list[dict[str, str]] = []
    known_bytes = 0
    unknown_sizes = 0
    for repository in document.repositories:
        for observation in repository.classes:
            if observation.content_class not in repository.requested_classes:
                exclusions.append(
                    {
                        "repository": repository.repository,
                        "content_class": observation.content_class,
                    }
                )
                continue
            reason = observation.reason
            if observation.status != "observed":
                reason = reason or "Content was not completely observed"
            elif observation.content_class not in TRANSFER_CLASSES:
                reason = "Transfer for this class is not implemented"
            else:
                reason = ""
            if reason:
                blockers.append(
                    {
                        "repository": repository.repository,
                        "content_class": observation.content_class,
                        "reason": reason,
                    }
                )
            for item in observation.objects:
                if item.size_bytes is None:
                    unknown_sizes += 1
                else:
                    known_bytes += item.size_bytes
    observed = all(
        c.status == "observed"
        for r in document.repositories
        for c in r.classes
        if c.content_class in r.requested_classes
    )
    return {
        "inventory_complete": observed,
        "transfer_ready": not blockers,
        "full_transfer": observed and not blockers and not exclusions,
        "blockers": blockers,
        "excluded_classes": exclusions,
        "known_payload_bytes": known_bytes,
        "objects_without_size": unknown_sizes,
        "temporary_space_assumption": (
            "At least 3x Git estimate plus 2x known payload bytes and 64 MiB; "
            "unknown sizes require separate capacity review, not a hard quota"
        ),
    }
