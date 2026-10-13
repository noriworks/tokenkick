"""Hermetic fixture entry point: real CLI contracts, synthetic providers only."""
import os
import pwd
from pathlib import Path
import socket
import subprocess
from unittest.mock import patch

from tokenkick import cli, discovery
from tokenkick.models import AccountState, AccountStatus, Config


def denied(*args, **kwargs):
    raise RuntimeError("Fixture generation forbids network and external processes")


def pairs(config=None, **kwargs):
    accounts = (config or Config.load()).accounts
    return accounts, [AccountStatus(a.label, AccountState.UNKNOWN, error="Synthetic fixture: no provider read")
                      for a in accounts], False, "Synthetic fixture accounts", []


if __name__ == "__main__":
    record = list(pwd.getpwuid(os.getuid()))
    record[5] = str(Path.home())
    with patch.object(pwd, "getpwuid", return_value=pwd.struct_passwd(record)), \
         patch.object(socket.socket, "connect", denied), patch.object(subprocess, "Popen", denied), \
         patch.object(cli, "_load_account_status_pairs", pairs), \
         patch.object(discovery, "_discover_direct_accounts", return_value=([], [])), \
         patch.object(discovery, "_discover_codexbar_accounts", return_value=([], [], False)), \
         patch.object(discovery, "_discover_codex_session_accounts", return_value=([], [])):
        cli.cli()
