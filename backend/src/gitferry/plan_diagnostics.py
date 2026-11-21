"""Closed, credential-free diagnostics shared by plan execution and its interfaces."""

from enum import Enum

from gitferry.state_store import OperationBlocked


class PreflightCheck(Enum):
    SELECTION = "selection: review supported content and target selection"
    STORAGE = "storage: check available package and workspace capacity"
    CONNECTION = "connection: check local credentials, CA and approved target identity"
    ADMINISTRATOR = "administrator: verify the separate technical administrator account"
    STRUCTURE = "structure: resolve organization, repository, team or membership prerequisites"
    AUTHOR_CREDENTIAL = "author_credential: provide the explicitly bound author's local token"
    AUTHOR_IDENTITY = "author_identity: verify the token belongs to the bound non-admin account"
    METADATA = (
        "metadata: inspect access, parent mapping, baseline and supported label prerequisites"
    )
    BUNDLE = "bundle: verify package integrity, repository identity and expected target refs"


class PreflightBlocked(OperationBlocked):
    def __init__(self, check: PreflightCheck):
        # Only the closed catalog reaches durable diagnostics, never exception text.
        super().__init__(check.value)
        self.check = check
