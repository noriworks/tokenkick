"""Render a system service for explicit user, home and installed tk paths.

Prints the unit only; it does not install, start or enable anything.
"""
import argparse
from pathlib import Path
import re


def render(user: str, home: str, binary: str, *, telegram: bool = False) -> str:
    if not re.fullmatch(r'[a-z_][a-z0-9_-]*', user):
        raise ValueError('Expected a Linux login name')
    for path in (home, binary):
        if not Path(path).is_absolute() or any(c in path for c in '\n\r\x00%"\\'):
            raise ValueError('Expected an absolute path without systemd specifiers or control characters')
    command = 'remote telegram' if telegram else 'daemon'
    return f'''[Unit]
Description=TokenKick {'Telegram remote' if telegram else 'quota daemon'}
After=network-online.target

[Service]
Type=simple
User={user}
Environment="HOME={home}"
Environment="PATH={home}/.local/bin:/usr/local/bin:/usr/bin:/bin"
ExecStart="{binary}" {command}
Restart=on-failure
RestartSec=30
TimeoutStopSec=360
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
'''


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--user', required=True)
    parser.add_argument('--home', required=True)
    parser.add_argument('--binary', required=True)
    parser.add_argument('--telegram', action='store_true')
    args = parser.parse_args()
    print(render(args.user, args.home, args.binary, telegram=args.telegram), end='')
