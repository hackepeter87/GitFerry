"""Launch the installed OCI application with its production cookie policy and UID."""

from __future__ import annotations

import json
import os
from pathlib import Path

import gitferry
from gitferry.server import main


def serve(root: Path) -> None:
    # Fixture setup needs local container administration; the application does not.
    # Only this fixed, run-owned context is handed to the image's normal runtime UID.
    if root.parent != Path("/state") or root.name not in {
        "source-export",
        "package-admission",
        "package-collision",
    }:
        raise RuntimeError("unexpected browser runtime context")
    for path in [root, *root.rglob("*")]:
        if path.is_symlink():
            raise RuntimeError("fixture runtime contains a symbolic link")
        os.chown(path, 10001, 10001)
    os.setgroups([])
    os.setgid(10001)
    os.setuid(10001)
    module = Path(gitferry.__file__).resolve()
    if not str(module).startswith("/usr/local/lib/"):
        raise RuntimeError("application was not imported from the installed image")
    (root / "browser-runtime.json").write_text(
        json.dumps(
            {
                "uid": os.getuid(),
                "gid": os.getgid(),
                "installed_application": True,
                "production_entrypoint": "gitferry.server",
                "secure_cookie_policy": True,
            }
        )
    )
    os.environ["GITFERRY_HOST"] = "127.0.0.1"
    main()
