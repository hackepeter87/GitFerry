from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).parents[2]
DOCKERFILE = ROOT / "containers" / "gitferry.Dockerfile"


def test_runtime_image_contract_is_non_root_persistent_and_health_checked() -> None:
    content = DOCKERFILE.read_text(encoding="utf-8")

    assert "USER 10001:10001" in content
    assert "GITFERRY_RUNTIME_MANAGED=1" in content
    assert "GITFERRY_STATE_DB=/var/lib/gitferry/state/gitferry.sqlite3" in content
    assert "GITFERRY_PACKAGE_DIR=/var/lib/gitferry/packages" in content
    assert "GITFERRY_WORK_DIR=/var/lib/gitferry/work" in content
    assert "GITFERRY_FRONTEND_DIR=/app/frontend-dist" in content
    assert "COPY --from=frontend-build /build/frontend/dist ./frontend-dist" in content
    assert "HEALTHCHECK" in content
    assert 'CMD ["python", "-m", "gitferry.healthcheck"]' in content
    assert "STOPSIGNAL SIGTERM" in content


def test_frontend_build_uses_immutable_node_image_and_locked_dependencies() -> None:
    content = DOCKERFILE.read_text(encoding="utf-8")

    assert re.search(
        r"^FROM node:24\.14\.0-bookworm-slim@sha256:[0-9a-f]{64} AS frontend-build$",
        content,
        re.MULTILINE,
    )
    assert "RUN npm ci --ignore-scripts" in content
    assert "RUN npm run build" in content


def test_runtime_image_contract_binds_version_revision_and_license() -> None:
    content = DOCKERFILE.read_text(encoding="utf-8")

    assert "ARG GITFERRY_VERSION=" in content
    assert "ARG GITFERRY_SOURCE_REVISION=" in content
    assert 'org.opencontainers.image.version="$GITFERRY_VERSION"' in content
    assert 'org.opencontainers.image.revision="$GITFERRY_SOURCE_REVISION"' in content
    assert 'org.opencontainers.image.licenses="Apache-2.0"' in content


def test_build_version_matches_python_package_metadata() -> None:
    metadata = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    match = re.search(r'^version = "([^"]+)"$', metadata, re.MULTILINE)
    assert match is not None
    version = match.group(1)
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")

    assert f"ARG GITFERRY_VERSION={version}" in DOCKERFILE.read_text(encoding="utf-8")
    assert f"GITFERRY_VERSION := {version}" in makefile


def test_candidate_version_is_shared_by_api_frontend_and_build() -> None:
    import json

    from gitferry import __version__
    from gitferry.api import create_app

    assert create_app().version == __version__
    package = json.loads((ROOT / "frontend/package.json").read_text())
    lock = json.loads((ROOT / "frontend/package-lock.json").read_text())
    assert package["version"] == lock["version"] == lock["packages"][""]["version"]
    assert package["version"].replace("-rc.", "rc") == __version__
    assert (
        f"GITFERRY_VERSION: ${{GITFERRY_VERSION:-{__version__}}}"
        in (ROOT / "tests/integration/compose.yaml").read_text()
    )

    runner = (ROOT / "tests/integration/run.sh").read_text()
    probe = (ROOT / "tests/integration/oci_runtime_probe.py").read_text()
    assert "from gitferry import __version__; print(__version__)" in runner
    assert 'labels.get("org.opencontainers.image.version") == __version__' in probe
