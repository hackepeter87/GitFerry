from types import SimpleNamespace

from gitferry import git_return_reconciliation
from gitferry.runtime import RuntimePaths


class References:
    def token(self, reference):
        assert reference == "target-token"
        return "synthetic-token"

    def tls(self, reference):
        assert reference is None
        return SimpleNamespace(get_ca_certs=lambda **_: []), "system-ca"


class Profiles:
    def __init__(self, state):
        self.state = state

    def get(self, instance_key):
        assert instance_key == "instance-a"
        return SimpleNamespace(
            instance_key="instance-a",
            revision=4,
            credential_ref="target-token",
            ca_ref=None,
            base_url="https://gitea-a.invalid",
        )


class Target:
    observed = None

    def __init__(self, package_root, work_root, base_url, username, token, *, ca_file=None):
        assert package_root.name == "packages"
        assert work_root.parent.name == "work"
        assert base_url == "https://gitea-a.invalid"
        assert username == "technical-admin"
        assert token == "synthetic-token"  # noqa: S105 - synthetic fixture only
        assert ca_file is None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return None

    def read_repository_refs(self, repository, *, expected_repository_id):
        assert repository == "selected/repository"
        assert expected_repository_id == "701"
        return {"refs/heads/main": "b" * 40}


def test_resolution_reads_exact_target_through_locally_approved_profile(tmp_path, monkeypatch):
    operation = SimpleNamespace(
        status="outcome_unknown",
        operation_type="push_git_ref",
        target_instance_key="instance-a",
        target_repository_scope="selected/repository",
        intent={"repository_id": "701", "ref": "refs/heads/main", "desired_oid": "b" * 40},
    )
    state = SimpleNamespace(get_operation=lambda operation_id: operation)
    paths = RuntimePaths(
        tmp_path / "state.sqlite3",
        tmp_path / "packages",
        tmp_path / "work",
    )
    paths.package_directory.mkdir()
    paths.work_directory.mkdir()
    monkeypatch.setattr(git_return_reconciliation, "ProfileService", Profiles)
    monkeypatch.setattr(
        git_return_reconciliation,
        "probe_connection",
        lambda profiles, instance_key, *, expected_revision, references: {
            "requires_approval": False,
            "observed_identity": {"ca_digest": "system-ca"},
            "login": "technical-admin",
        },
    )
    monkeypatch.setattr(git_return_reconciliation, "GiteaBundlePusher", Target)

    captured = {}

    def reconcile(store, operation_id, live_read, *, evidence):
        captured.update(operation_id=operation_id, evidence=evidence, live=live_read())
        return SimpleNamespace(operation_id=operation_id, status="confirmed")

    monkeypatch.setattr(git_return_reconciliation, "reconcile_git_ref_operation", reconcile)
    result = git_return_reconciliation.resolve_git_ref_operation(
        state,
        paths,
        "git-return-operation",
        evidence="Operator verified live target",
        references=References(),
    )

    assert result.status == "confirmed"
    assert captured == {
        "operation_id": "git-return-operation",
        "evidence": "Operator verified live target",
        "live": {
            "target_instance_key": "instance-a",
            "repository_id": "701",
            "repository_scope": "selected/repository",
            "refs": {"refs/heads/main": "b" * 40},
        },
    }
