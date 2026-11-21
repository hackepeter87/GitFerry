#!/usr/bin/env python3
"""Provision one synthetic AD forest using Samba's Python provision API."""

from __future__ import annotations

import logging
import os
from pathlib import Path

from samba.auth import system_session
from samba.param import LoadParm
from samba.provision import provision

ROOT = Path("/var/lib/samba")
CONF = "/etc/samba/smb.conf"
ADMIN_PASSWORD = os.environ.get("AD_ADMIN_PASSWORD", "Synthetic-Admin-Only-42!")

lp = LoadParm()
lp.load(CONF)
provision(
    logger=logging.getLogger("samba.provision"),
    lp=lp,
    session_info=system_session(),
    smbconf=CONF,
    targetdir=str(ROOT),
    hostname="synthdc",
    realm="SYNTH.AD",
    domain="SYNTH",
    serverrole="active directory domain controller",
    adminpass=ADMIN_PASSWORD,
    use_rfc2307=True,
    dns_backend="SAMBA_INTERNAL",
)
