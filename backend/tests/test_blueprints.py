from pathlib import Path

import pytest
from gitferry.blueprints import BlueprintValidationError, load_and_validate

ROOT = Path(__file__).parents[2]


@pytest.mark.parametrize(
    "filename,kind",
    [
        ("instance-blueprint.yaml", "InstanceBlueprint"),
        ("transfer-blueprint.yaml", "TransferBlueprint"),
    ],
)
def test_examples_validate(filename: str, kind: str) -> None:
    document = load_and_validate(ROOT / "examples" / filename)
    assert document["apiVersion"] == "gitferry.io/v1"
    assert document["kind"] == kind


def test_plaintext_secret_field_is_rejected(tmp_path: Path) -> None:
    blueprint = tmp_path / "unsafe.yaml"
    blueprint.write_text(
        """apiVersion: gitferry.io/v1
kind: InstanceBlueprint
metadata: {name: unsafe}
spec:
  gitea:
    baseUrl: https://gitea.example.invalid
    credentialSecretRef: secrets/gitea/token
    password: plaintext
""",
        encoding="utf-8",
    )

    with pytest.raises(BlueprintValidationError, match="password"):
        load_and_validate(blueprint)


def test_unknown_api_version_is_rejected(tmp_path: Path) -> None:
    blueprint = tmp_path / "future.yaml"
    blueprint.write_text(
        """apiVersion: gitferry.io/v99
kind: TransferBlueprint
metadata: {name: future}
spec: {}
""",
        encoding="utf-8",
    )

    with pytest.raises(BlueprintValidationError, match="apiVersion"):
        load_and_validate(blueprint)


def test_invalid_transfer_mode_is_rejected(tmp_path: Path) -> None:
    content = (ROOT / "examples" / "transfer-blueprint.yaml").read_text(encoding="utf-8")
    blueprint = tmp_path / "invalid-mode.yaml"
    blueprint.write_text(content.replace("mode: bootstrap", "mode: mirror"), encoding="utf-8")

    with pytest.raises(BlueprintValidationError, match="mode"):
        load_and_validate(blueprint)


def test_identity_mapping_requires_external_id_and_ou_path(tmp_path: Path) -> None:
    content = (ROOT / "examples" / "transfer-blueprint.yaml").read_text(encoding="utf-8")
    blueprint = tmp_path / "underspecified-identity.yaml"
    blueprint.write_text(
        content.replace("        sourceExternalId: S-1-5-21-A-1001\n", "").replace(
            "        sourceOuPath: OU=People,DC=a,DC=example,DC=invalid\n", ""
        ),
        encoding="utf-8",
    )

    with pytest.raises(BlueprintValidationError, match="sourceExternalId"):
        load_and_validate(blueprint)
