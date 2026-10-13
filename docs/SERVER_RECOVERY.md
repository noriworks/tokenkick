# Server upgrade and recovery

Use the existing application user for package installation and provider commands.
Use root only for process ownership checks, service management, and protected backups.
Never print configuration, credential files, provider transcripts, or unfiltered logs.

## Before upgrading

1. Record the installed version, exact pipx environment path, and which TokenKick
   processes/services are running. Record service unit files and enabled states.
2. Verify PID ownership and argv before stopping a legacy daemon/listener. New
   TokenKick releases additionally verify process creation identity. Do not use
   a legacy PID file alone to choose a process to signal.
3. Stop the daemon and Telegram listener so application state is quiescent.
4. Archive the entire application state directory, the pipx environment, pipx
   metadata/entry-point links, and existing service units into a root-owned 0700
   backup directory. Credential backups must remain 0600 and never enter Git.
5. Extract into a separate root-only directory. Verify file hashes and launch the
   restored environment's Python with an isolated HOME to check its package version.
6. Keep a SHA-256-verified off-host copy in an owner-only directory. The application
   user must not be able to modify either protected backup.

## Upgrade and verify

Install the exact published version with pipx under the application user. Check
`tk --version`, run a package dependency check, and load config once to apply
migrations. Confirm all unrelated account settings and automation flags survived.
Restart only the previously enabled/running components and inspect their owned
PID/version information. Keep the existing service strategy unless deliberately
migrating it with a separate verified unit definition.

Refresh providers explicitly. Separate catalog/auth checks from generation; a
successful CLI exit does not prove a quota window started. For a quota-consuming
smoke test, send one bounded native request per intended provider, record its
model/output/token evidence, and inspect the before/after provider quota state.
Do not repeatedly fire requests to make a reset clock move. Record unavailable
provider data as a limitation instead of reporting a verified anchor.

## Rollback (explicit operator authorization required)

1. Stop the new daemon and listener with their verified process identities.
2. Preserve the post-upgrade state and installation in a separate protected
   snapshot. Do not overwrite newer history or credentials without reviewing
   what changed since the backup.
3. Restore the prior pipx environment to its original absolute path; restore its
   entry-point links and metadata. The environment's scripts use absolute paths.
4. Restore application config/state only when necessary, after reviewing the
   post-upgrade changes. Spark account migration is reversible from the backup;
   credential homes and history are not removed by that migration.
5. Restore service definitions/enabled states if they were changed, reload
   systemd, and restart the previously running components as the application user.
6. Check the restored package version, owned PID/version metadata, cached status,
   and provider authentication. Keep both snapshots until recovery is accepted.

A release must retain the protected backup paths and checksums in its operator
record. Keep account-specific paths and credentials out of public release notes.
