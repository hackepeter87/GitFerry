from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).parents[2]


def deployment() -> dict[str, object]:
    value = yaml.safe_load((ROOT / "deploy" / "compose.yaml").read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def test_reference_deployment_is_non_root_unprivileged_and_persistent() -> None:
    document = deployment()
    service = document["services"]["gitferry"]

    assert service["user"] == "10001:10001"
    assert service["read_only"] is True
    assert service["cap_drop"] == ["ALL"]
    assert service["security_opt"] == ["no-new-privileges:true"]
    assert service.get("privileged") is None
    assert "gitferry-data:/var/lib/gitferry" in service["volumes"]
    assert all("docker.sock" not in str(volume) for volume in service["volumes"])
    assert service["healthcheck"]["test"] == [
        "CMD",
        "python",
        "-m",
        "gitferry.healthcheck",
    ]


def test_reference_deployment_uses_secret_and_ca_references() -> None:
    document = deployment()
    service = document["services"]["gitferry"]

    assert service["secrets"] == ["source_api_token", "target_api_token"]
    assert set(document["secrets"]) == {"source_api_token", "target_api_token"}
    assert service["environment"]["GITFERRY_SOURCE_TOKEN_FILE"].startswith("/run/secrets/")
    assert service["environment"]["GITFERRY_TARGET_TOKEN_FILE"].startswith("/run/secrets/")
    ca_mount = next(volume for volume in service["volumes"] if isinstance(volume, dict))
    assert ca_mount["target"] == "/etc/gitferry/ca"
    assert ca_mount["read_only"] is True
