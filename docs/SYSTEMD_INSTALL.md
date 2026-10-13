# Installing on a Linux server

Resolve the account's actual home and `tk` executable **as the service user** after
installing with pipx. `getent passwd USER` reports the home; `sudo -iu USER sh -lc
'command -v tk'` reports the executable. Do not assume `/home/USER` or
`/usr/local/bin/tk`. The legacy `tokenkick@.service` templates require path overrides.

Generate a concrete unit with those values, review it, then install it:

```sh
python3 scripts/render-systemd-unit.py --user tokenkick --home /srv/tokenkick --binary /srv/tokenkick/.local/bin/tk > /tmp/tokenkick.service
cat /tmp/tokenkick.service
sudo install -m 0644 /tmp/tokenkick.service /etc/systemd/system/tokenkick.service
sudo systemctl daemon-reload
sudo systemctl enable --now tokenkick.service
sudo systemctl status tokenkick.service
```

The paths above are examples. The generator does not install or start services.
Use `--telegram` and a distinct `tokenkick-telegram.service` name for a separate
remote listener. Do not enable both a template instance and the concrete unit for
the same account. Configure provider binary paths in the unit's PATH if needed.

The 360-second stop allowance lets an in-progress request and its verification
finish. After updating the installed package, restart the daemon and inspect
`tk daemon --status --json-output` before enabling ongoing stagger. TokenKick
refuses to grant stagger ownership while an older running daemon lacks the
required runtime capabilities. Preserve config and state backups during upgrades.
