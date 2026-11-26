"""Primary and historical Gitea contracts used by live integration probes."""

from __future__ import annotations

import os

PRIMARY_GITEA_VERSION = "28.4.0"
PRIMARY_GITEA_IMAGE = (
    "docker.gitea.com/gitea:28.4.0@sha256:"
    "aa90e8f00b755b0d665214ab9c14447ea44d3190dbe6b6d0cb46cd9c4f55d309"
)
PRIMARY_GITEA_PLATFORM_DIGESTS = {
    "linux/amd64": "sha256:1565a01ff935d56156285149acfce48ecc19c5d4e8ed278bebcf06f44e148295",
    "linux/arm64": "sha256:93f860dce19a3bb924361c167c93c7a2a93d74a5600060772c643cbf65de01f1",
    "linux/riscv64": "sha256:e3b3751ed7f47dd611ca79a3070baf09ba2c790ac2b5e5b03ef9cdb64768d4f5",
}
HISTORICAL_GITEA_VERSION = "1.27.2"
HISTORICAL_GITEA_IMAGE = (
    "docker.gitea.com/gitea:1.27.2@sha256:"
    "d20286ca2b2e170fdf628e7231b8a31a3220ade39ff462b55041d43d1fc757dd"
)


def expected_source_version() -> str:
    """Return the source version required by the current live run."""

    return os.environ.get("GITFERRY_IT_EXPECTED_SOURCE_GITEA_VERSION", PRIMARY_GITEA_VERSION)


def expected_target_version() -> str:
    """Return the target version required by the current live run."""

    return os.environ.get("GITFERRY_IT_EXPECTED_TARGET_GITEA_VERSION", PRIMARY_GITEA_VERSION)
