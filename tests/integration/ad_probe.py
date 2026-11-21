"""Separate Samba AD-compatible claim evidence; not Microsoft AD qualification."""

from __future__ import annotations

import json
import subprocess
import time
from typing import Any

import httpx
from ad_gitea_worker import run_ad_gitea_probe
from support import COMPOSE, TARGET_URL, ProbeFailure, docker_service_exec, run


def run_ad_probe() -> dict[str, Any]:
    result = docker_service_exec("ad", ["python3", "/usr/local/bin/ad-worker"], check=False)
    if result.returncode:
        raise ProbeFailure("Samba AD claim worker did not complete")
    records = [json.loads(line) for line in result.stdout.splitlines() if line.strip()]
    if len(records) != 3:
        raise ProbeFailure("Samba AD claim worker evidence is incomplete")
    facts, claim, checks = records
    container = run([*COMPOSE, "ps", "-q", "ad"]).stdout.strip()
    image = run(["docker", "inspect", "--format", "{{.Image}}", container]).stdout.strip()
    version = docker_service_exec("ad", ["samba", "--version"]).stdout.strip()
    platform = run(
        ["docker", "image", "inspect", image, "--format", "{{.Os}}/{{.Architecture}}"]
    ).stdout.strip()
    negatives = checks.get("negative_assertions", {})
    forms = checks.get("credential_forms", {})
    observed = claim.get("application_observe_claim", {})
    if (
        facts.get("status") != "passed"
        or any(
            negatives.get(key) != "passed"
            for key in ("wrong_password", "wrong_ca", "disabled_filter")
        )
        or any(forms.get(key) is not True for key in ("upn", "domain_login"))
        or observed.get("external_id_kind") != "sid"
        or not str(observed.get("external_id", "")).startswith("S-1-5-21-")
        or "email" not in observed
        or observed["email"] is not None
        or observed.get("groups") != ["CN=Direct,OU=Groups,DC=synth,DC=ad"]
        or set(checks.get("nested_matching_rule_results", []))
        != {"CN=Direct,OU=Groups,DC=synth,DC=ad", "CN=Nested,OU=Groups,DC=synth,DC=ad"}
    ):
        raise ProbeFailure("Samba AD claim assertions are incomplete")
    return {
        "status": "passed",
        "server_version": version,
        "image_id": image,
        "platform": platform,
        "client": "ldap3 2.9.1",
        "claims": observed,
        "gitea": run_ad_gitea_qualification(),
        "negative_assertions": negatives,
        "credential_forms": forms,
        "nested_matching_rule_results": checks["nested_matching_rule_results"],
        "limits": [
            "Samba AD-compatible server; not Microsoft Windows AD",
            "Product observes direct groups; nested query qualifies server behavior only",
            "Gitea authentication and application permission flow remain separately qualified",
            "Explicit OU search base avoids unsupported referral traversal",
        ],
    }


def run_ad_gitea_qualification() -> dict[str, Any]:
    """Attach the public AD CA to the existing CA volume, then qualify Gitea."""
    ca = docker_service_exec("ad", ["cat", "/run/ad-ca/ca.pem"]).stdout
    command = [*COMPOSE, "exec", "-T", "openldap", "sh", "-c"]
    trust_command = (
        "cat > /run/directory-ca/ad-ca.pem && "
        "cat /run/directory-ca/ca.pem /run/directory-ca/ad-ca.pem "
        "> /run/directory-ca/combined.pem && "
        "cp /run/directory-ca/combined.pem /run/directory-ca/ca.pem"
    )
    append = subprocess.run(
        [
            *command,
            trust_command,
        ],
        input=ca,
        text=True,
        capture_output=True,
        check=False,
        timeout=30,
    )
    if append.returncode:
        raise ProbeFailure("could not append the synthetic AD CA to the target trust volume")
    run([*COMPOSE, "restart", "target"], timeout=60)
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        try:
            if httpx.get(f"{TARGET_URL}/api/v1/version", timeout=2).status_code == 200:
                return run_ad_gitea_probe()
        except httpx.HTTPError:
            pass
        time.sleep(1)
    raise ProbeFailure("Gitea did not recover after AD CA trust restart")
