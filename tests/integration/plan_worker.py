"""Exercise application plans in the target-only Linux container with real Gitea."""

from __future__ import annotations

import argparse
import json
import os
import socket
import ssl
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import httpx
from gitferry.application_package import inspect_package
from gitferry.comment_operations import resolve_unknown_comment_operation
from gitferry.connection_probe import LocalReferences, approve_connection, probe_connection
from gitferry.connection_profiles import ConnectionProfile, ProfileService
from gitferry.gitea_comments import CommentTargetConflict, GiteaCommentClient
from gitferry.gitea_plan_adapter import GiteaPlanAdapter
from gitferry.package_archive import receive_package_archive
from gitferry.plan_application import prepare_transfer_plan
from gitferry.plan_runner import PlanRunner
from gitferry.runtime import RuntimePaths
from gitferry.source_catalog import browse_source
from gitferry.state_store import OperationBlocked, OriginKey, StateStore
from gitferry.transfer_plans import PlanService

ROOT = Path("/state/application-plans")
DATABASE = ROOT / "state.sqlite"
URL = "http://target:3000"
HTTPS = "https://localhost:34443"
ORG = "application-plan-lab"
REPOSITORY = ORG + "/selected"
ADMIN = "gitferry-admin"


def remote(method: str, path: str, *, username: str = ADMIN, **kwargs: Any) -> Any:
    password = os.environ[
        "GITFERRY_IT_ADMIN_PASSWORD" if username == ADMIN else "GITFERRY_IT_USER_PASSWORD"
    ]
    response = httpx.request(
        method,
        URL + "/api/v1" + path,
        auth=(username, password),
        timeout=20,
        trust_env=False,
        **kwargs,
    )
    response.raise_for_status()
    return response.json() if response.content else None


@contextmanager
def proxy(
    *,
    lose_comment_response: bool = False,
    lose_asset_response: bool = False,
    preserve_origin: bool = False,
    lose_lfs_response: bool = False,
):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args: Any) -> None:
            pass

        def forward(self) -> None:
            body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
            forwarded = {
                key: value
                for key, value in self.headers.items()
                if key.lower() not in ("host", "content-length", "connection")
            }
            if preserve_origin:
                # The approved TLS fixture has one fixed public origin. Gitea's
                # LFS action URLs must describe that origin, not its internal hop.
                forwarded.update(
                    {
                        "Host": "localhost:34443",
                        "X-Forwarded-Host": "localhost:34443",
                        "X-Forwarded-Proto": "https",
                    }
                )
            response = httpx.request(
                self.command,
                URL + self.path,
                content=body,
                headers=forwarded,
                timeout=30,
                trust_env=False,
                follow_redirects=False,
            )
            response_content = response.content
            if preserve_origin and response.headers.get("content-type", "").startswith(
                "application/json"
            ):
                response_content = response_content.replace(
                    b"http://target:3000/attachments/",
                    b"https://localhost:34443/attachments/",
                )
            if self.command in ("POST", "PATCH", "PUT"):
                with (ROOT / "remote-events.jsonl").open("a") as stream:
                    stream.write(
                        json.dumps(
                            {
                                "method": self.command,
                                "path": self.path,
                                "status": response.status_code,
                                **(
                                    {"lfs_operation": json.loads(body).get("operation")}
                                    if self.path.endswith("/objects/batch")
                                    else {}
                                ),
                            }
                        )
                        + "\n"
                    )
                    stream.flush()
                    os.fsync(stream.fileno())
            if (
                lose_lfs_response
                and self.command == "PUT"
                and "/info/lfs/objects/" in self.path
                and response.status_code in (200, 201, 204)
            ):
                marker = ROOT / "lfs-response-lost.json"
                with marker.open("w") as stream:
                    json.dump(
                        {"remote_status": response.status_code, "response_dropped": True}, stream
                    )
                    stream.flush()
                    os.fsync(stream.fileno())
                self.connection.shutdown(socket.SHUT_RDWR)
                self.connection.close()
                return
            if (
                lose_comment_response
                and self.command == "POST"
                and self.path.endswith("/comments")
                and response.status_code == 201
            ):
                self.connection.shutdown(socket.SHUT_RDWR)
                self.connection.close()
                return
            if (
                lose_asset_response
                and self.command == "POST"
                and self.path.endswith("/assets")
                and response.status_code == 201
            ):
                self.connection.shutdown(socket.SHUT_RDWR)
                self.connection.close()
                return
            self.send_response(response.status_code)
            self.send_header(
                "Content-Type", response.headers.get("content-type", "application/octet-stream")
            )
            self.send_header("Content-Length", str(len(response_content)))
            for header in ("WWW-Authenticate", "X-Total-Count", "Link"):
                if header in response.headers:
                    self.send_header(header, response.headers[header])
            self.end_headers()
            self.wfile.write(response_content)

        do_GET = do_POST = do_PUT = do_PATCH = forward

    server = ThreadingHTTPServer(("127.0.0.1", 34443), Handler)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(str(ROOT / "trusted.pem"), str(ROOT / "trusted.key"))
    server.socket = context.wrap_socket(server.socket, server_side=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        if thread.is_alive():
            raise RuntimeError("plan TLS proxy did not stop")


def author(username: str) -> dict[str, str]:
    return {
        "realm_key": "synthetic-source",
        "login": username,
        "external_id": "source-" + username,
        "ou_path": "OU=Source",
    }


def initialize(state: StateStore) -> dict[str, Any]:
    for directory in (ROOT / "work", ROOT / "packages"):
        directory.mkdir(mode=0o700, exist_ok=True)
    secrets = ROOT / "secrets"
    secrets.mkdir(mode=0o700, exist_ok=True)
    for username in (ADMIN, "issue-author", "comment-author"):
        token = remote(
            "POST",
            f"/users/{username}/tokens",
            username=username,
            json={"name": "application-plan-probe", "scopes": ["all"]},
        )
        path = secrets / username
        path.write_text(token["sha1"])
        path.chmod(0o600)
    profiles = ProfileService(state)
    profiles.create(
        ConnectionProfile(
            "target", "Synthetic target", HTTPS, "target-plan-install", ADMIN, "trusted.pem"
        )
    )
    references = LocalReferences(secrets, ROOT)
    evidence = probe_connection(profiles, "target", expected_revision=1, references=references)
    approve_connection(
        profiles, "target", expected_revision=1, evidence_digest=evidence["evidence_digest"]
    )
    state.register_instance(
        "source", "Synthetic source", "https://source.invalid", "source-plan-install"
    )
    state.register_realm("synthetic-source", "Source realm", "synthetic-directory")
    for username in ("issue-author", "comment-author"):
        target = remote("GET", f"/users/{username}")
        state.confirm_identity_binding(
            binding_id=username,
            source_realm_key="synthetic-source",
            source_login=username,
            source_external_id="source-" + username,
            source_ou_path="OU=Source",
            target_instance_key="target",
            target_account_id=target["id"],
            target_login=username,
            mapping_version=1,
        )
    organization = state.register_origin(OriginKey("source", ORG, "organization", "50"))
    repository = state.register_origin(OriginKey("source", REPOSITORY, "repository", "51"))
    team = state.register_origin(OriginKey("source", ORG, "team", "52"))
    issue = state.register_origin(OriginKey("source", REPOSITORY, "issue", "53", 1))
    comment = state.register_origin(
        OriginKey("source", REPOSITORY, "comment", "54", parent_origin_id=issue.id)
    )
    # Only the archive crosses the boundary. The receiver has no source connection.
    package = receive_package_archive(
        Path("/package/application-plan.zip"), ROOT / "packages" / "initial"
    )
    selected = package.document["repositories"][0]
    if selected["repository"] != REPOSITORY or package.document["recipient"] != {
        "instance_key": "target",
        "installation_marker": "target-plan-install",
    }:
        raise RuntimeError("received package scope or target disagrees with the local plan")
    payload = selected["bundle"]
    bundle = {"member": payload["path"], "sha256": payload["sha256"], "size": payload["size"]}
    origins: dict[tuple[str, str], str] = {("issue", "53"): issue.id}
    for item in package.document.get("content", []):
        if item["kind"] not in ("label", "release"):
            continue
        portable = item["origin"]
        record = state.register_origin(
            OriginKey(
                portable["instance_key"],
                portable["repository"],
                portable["object_type"],
                portable["global_id"],
            )
        )
        origins[(portable["object_type"], portable["global_id"])] = record.id
    for item in package.document.get("content", []):
        if item["kind"] not in ("attachment", "release_asset"):
            continue
        portable = item["origin"]
        parent = item["parent_origin"]
        record = state.register_origin(
            OriginKey(
                portable["instance_key"],
                portable["repository"],
                portable["object_type"],
                portable["global_id"],
                parent_origin_id=origins[(parent["object_type"], parent["global_id"])],
            )
        )
        origins[(portable["object_type"], portable["global_id"])] = record.id
    content = []
    for item in package.document.get("content", []):
        values = item.get("values")
        if item["kind"] in ("attachment", "release_asset"):
            parent = item["parent_origin"]
            values = {
                "name": item["name"],
                "size": item["file"]["size"],
                "sha256": item["file"]["sha256"],
                "media_type": item["media_type"],
                "member": item["file"]["path"],
                "parent_origin_id": origins[(parent["object_type"], parent["global_id"])],
            }
        content.append(
            {
                "origin_id": origins[(item["origin"]["object_type"], item["origin"]["global_id"])],
                "repository": item["repository"],
                "kind": item["kind"],
                "values": values,
                **({"author": item["author"]} if "author" in item else {}),
            }
        )
    for assignment in package.document.get("label_assignments", []):
        issue_origin = assignment["issue_origin"]
        label_origins = assignment["label_origins"]
        content.append(
            {
                "origin_id": origins[(issue_origin["object_type"], issue_origin["global_id"])],
                "repository": assignment["repository"],
                "kind": "label_assignment",
                "values": {
                    "label_origin_ids": [
                        origins[(label["object_type"], label["global_id"])]
                        for label in label_origins
                    ]
                },
            }
        )
    request = {
        "version": 1,
        "target_instance_key": "target",
        "round_key": "initial",
        "package_ref": "initial",
        "repositories": [
            {
                "organization_origin_id": organization.id,
                "organization": ORG,
                "repository_origin_id": repository.id,
                "repository": REPOSITORY,
                "teams": [
                    {
                        "origin_id": team.id,
                        "name": "writers",
                        "permission": "write",
                        "members": [
                            {"binding_id": name, "role": "write"}
                            for name in ("issue-author", "comment-author")
                        ],
                    }
                ],
                "bundle": bundle,
            }
        ],
        "metadata": [
            {
                "origin_id": comment.id,
                "repository": REPOSITORY,
                "author": author("comment-author"),
                "values": {"body": "Fähre 🚢\n**Kommentar**"},
            },
            {
                "origin_id": issue.id,
                "repository": REPOSITORY,
                "author": author("issue-author"),
                "values": {
                    "title": "Application plan",
                    "body": "Issue body",
                    "state": "open",
                    "labels": [],
                },
            },
        ],
        "content": content,
        "author_credential_refs": {name: name for name in ("issue-author", "comment-author")},
    }
    (ROOT / "request.json").write_text(json.dumps(request))
    return prepare_transfer_plan(state, request)


def isolated() -> bool:
    try:
        with socket.create_connection(("source", 3000), timeout=2):
            return False
    except OSError:
        return True


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "mode",
        choices=(
            "initial",
            "replay",
            "crash",
            "lost",
            "asset-lost",
            "metadata-update",
            "metadata-drift",
            "label-collision",
            "release-collision",
            "metadata-omission",
            "resume",
            "repackage",
            "resolve",
            "browse",
        ),
    )
    parser.add_argument("--case", default="initial")
    parser.add_argument("--target-comment-id")
    args = parser.parse_args()
    state = StateStore(DATABASE)
    with proxy(
        lose_comment_response=args.mode == "lost",
        lose_asset_response=args.mode == "asset-lost",
        preserve_origin=True,
    ):
        if args.mode == "browse":
            service = ProfileService(state)
            refs = LocalReferences(ROOT / "secrets", ROOT)
            result = {}
            for kind, organization in (("organizations", None), ("repositories", ORG)):
                result[kind] = browse_source(
                    service,
                    "target",
                    expected_revision=service.get("target").revision,
                    kind=kind,
                    organization=organization,
                    page=1,
                    references=refs,
                )
            print(json.dumps(result))
            return
        if args.mode == "initial":
            plan = initialize(state)
        elif args.mode == "asset-lost":
            request = json.loads((ROOT / "request.json").read_text())
            issue_origin_id = request["metadata"][1]["origin_id"]
            origin = state.register_origin(
                OriginKey(
                    "source",
                    REPOSITORY,
                    "attachment",
                    "61",
                    parent_origin_id=issue_origin_id,
                )
            )
            source_asset = next(item for item in request["content"] if item["kind"] == "attachment")
            request["content"] = [
                {
                    **source_asset,
                    "origin_id": origin.id,
                    "values": {
                        **source_asset["values"],
                        "name": "lost-response-proof.txt",
                    },
                }
            ]
            request["metadata"] = []
            request["repositories"] = []
            request["round_key"] = "asset-lost"
            (ROOT / "asset-lost-request.json").write_text(json.dumps(request))
            plan = prepare_transfer_plan(state, request)
        elif args.mode == "metadata-update":
            request = json.loads((ROOT / "request.json").read_text())
            request["metadata"] = []
            request["repositories"] = []
            request["round_key"] = "metadata-update"
            state.create_transfer_round("metadata-update-round", "source", "target", REPOSITORY)
            updates = []
            for item in request["content"]:
                if item["kind"] not in ("label", "release"):
                    continue
                baseline_id = "metadata-update-" + item["kind"]
                changed = dict(item["values"])
                if item["kind"] == "label":
                    changed.update(color="224466", description="Portable label updated on target")
                    ruleset = "gitea-label-fields"
                else:
                    changed.update(name="Portable release updated", body="Updated release notes")
                    ruleset = "gitea-release-fields"
                state.create_baseline(
                    baseline_id=baseline_id,
                    transfer_round_id="metadata-update-round",
                    origin_id=item["origin_id"],
                    ruleset_name=ruleset,
                    ruleset_version=1,
                    baseline=item["values"],
                    desired=changed,
                    observed=item["values"],
                    origin_created_at=None,
                    target_native_created_at=state.get_object_mapping(
                        item["origin_id"], "target"
                    ).target_native_created_at,
                )
                updates.append({**item, "baseline_id": baseline_id, "values": changed})
            request["content"] = updates
            (ROOT / "metadata-update-request.json").write_text(json.dumps(request))
            plan = prepare_transfer_plan(state, request)
        elif args.mode == "metadata-drift":
            request = json.loads((ROOT / "metadata-update-request.json").read_text())
            request["round_key"] = "metadata-drift"
            label = next(item for item in request["content"] if item["kind"] == "label")
            previous = dict(label["values"])
            desired = {**previous, "color": "445566", "description": "Desired second update"}
            state.create_transfer_round("metadata-drift-round", "source", "target", REPOSITORY)
            state.create_baseline(
                baseline_id="metadata-drift-label",
                transfer_round_id="metadata-drift-round",
                origin_id=label["origin_id"],
                ruleset_name="gitea-label-fields",
                ruleset_version=1,
                baseline=previous,
                desired=desired,
                observed=previous,
                origin_created_at=None,
                target_native_created_at=None,
            )
            label["baseline_id"] = "metadata-drift-label"
            label["values"] = desired
            request["content"] = [label]
            mapping = state.get_object_mapping(label["origin_id"], "target")
            remote(
                "PATCH",
                f"/repos/{REPOSITORY}/labels/{mapping.target_global_id}",
                json={
                    "name": previous["name"],
                    "color": "778899",
                    "description": "Foreign drift",
                },
            )
            (ROOT / "metadata-drift-request.json").write_text(json.dumps(request))
            plan = prepare_transfer_plan(state, request)
        elif args.mode in ("label-collision", "release-collision"):
            request = json.loads((ROOT / "request.json").read_text())
            kind = "label" if args.mode == "label-collision" else "release"
            source = next(item for item in request["content"] if item["kind"] == kind)
            origin = state.register_origin(
                OriginKey("source", REPOSITORY, kind, "62" if kind == "label" else "63")
            )
            request["repositories"] = []
            request["metadata"] = []
            request["content"] = [{**source, "origin_id": origin.id}]
            request["round_key"] = args.mode
            (ROOT / (args.mode + "-request.json")).write_text(json.dumps(request))
            plan = prepare_transfer_plan(state, request)
        elif args.mode == "metadata-omission":
            request = json.loads((ROOT / "request.json").read_text())
            request["repositories"] = []
            # Retain one already-confirmed non-asset object so the plan remains
            # a real replay while all portable metadata classes are omitted.
            request["metadata"] = request["metadata"][:1]
            request["content"] = []
            request["round_key"] = "metadata-omission"
            plan = prepare_transfer_plan(state, request)
        elif args.mode in ("crash", "lost"):
            request = json.loads((ROOT / "request.json").read_text())
            parent = request["metadata"][1]["origin_id"]
            origin = state.register_origin(
                OriginKey(
                    "source",
                    REPOSITORY,
                    "comment",
                    "55" if args.mode == "crash" else "56",
                    parent_origin_id=parent,
                )
            )
            request["metadata"] = [
                {
                    "origin_id": origin.id,
                    "repository": REPOSITORY,
                    "author": author("comment-author"),
                    "values": {"body": args.mode + " comment"},
                }
            ]
            request["repositories"] = []
            request["content"] = []
            request["round_key"] = args.mode
            (ROOT / (args.mode + "-request.json")).write_text(json.dumps(request))
            plan = prepare_transfer_plan(state, request)
        elif args.mode == "repackage":
            request = json.loads((ROOT / (args.case + "-request.json")).read_text())
            request["round_key"] += "-repackaged"
            try:
                prepare_transfer_plan(state, request)
            except OperationBlocked:
                print(json.dumps({"blocked": True, "isolated": isolated()}))
                return
            raise RuntimeError("new packaging bypassed an unknown operation")
        else:
            plan = PlanService(state).get((ROOT / (args.case + "-plan-id")).read_text())
        if args.mode == "resolve":
            if not args.target_comment_id:
                raise ValueError("explicit operator-selected target comment ID required")
            references = LocalReferences(ROOT / "secrets", ROOT)
            context, _ = references.tls("trusted.pem")
            with GiteaCommentClient(
                HTTPS,
                username="comment-author",
                token=references.token("comment-author"),
                verify=context,
            ) as client:
                try:
                    resolve_unknown_comment_operation(
                        state,
                        client,
                        plan["operations"][0]["operation_id"],
                        target_comment_id=args.target_comment_id,
                    )
                except (OperationBlocked, CommentTargetConflict):
                    print(
                        json.dumps(
                            {"rejected": True, "plan": PlanService(state).get(plan["plan_id"])}
                        )
                    )
                    return
            plan = PlanService(state).get(plan["plan_id"])
        (ROOT / (args.case + "-plan-id")).write_text(plan["plan_id"])
        service = PlanService(state)
        if plan["status"] not in ("running", "preflighting", "confirmed"):
            service.queue(plan["plan_id"], plan["digest"])

        def crash() -> None:
            with state.read_connection() as connection:
                executing = connection.execute(
                    "SELECT operation_type FROM operations WHERE status='executing'"
                ).fetchone()
            if executing and executing["operation_type"] == "create_comment":
                os._exit(91)

        paths = RuntimePaths(DATABASE, ROOT / "packages", ROOT / "work")
        runner = PlanRunner(
            service,
            lambda _: GiteaPlanAdapter(state, paths, LocalReferences(ROOT / "secrets", ROOT)),
            after_remote_hook=crash if args.mode == "crash" else None,
        )
        result = runner.run(plan["plan_id"])
        print(
            json.dumps(
                {
                    "plan": result,
                    "isolated": isolated(),
                    "package_digest": inspect_package(ROOT / "packages" / "initial").digest_sha256,
                }
            )
        )


if __name__ == "__main__":
    main()
