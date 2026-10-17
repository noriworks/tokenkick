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

## Disk headroom and backup retention

Before creating another release snapshot, measure free space and existing backup
sizes. Budget for both the compressed archive and its fully extracted restore
test, plus installation headroom. Repeatedly retaining extracted tests can fill
the server even when individual archives are small.

After a successful upgrade, propose retaining the two newest compressed rollback
archives and removing older archives and disposable restore-test trees. Backup
deletion requires explicit user authorization. Before cleanup, recheck the kept
archives against their recorded SHA-256 hashes and independently verify the
latest protected off-host copy. Keep restore-test results, checksums, deployment
records and cleanup manifests; mark removed artifacts so historical verification
records are not mistaken for available backups.

Use an exact allowlist of inspected paths, reject symlinks or nested mounts, and
check that no running process uses them. Never include live account homes,
credentials, history or unrelated applications in release cleanup. Verify free
space and service/configuration health afterward. Restoring an archive remains
a separate, explicitly authorized operation.

## Scoped request-guard recovery

An enabled account can still be blocked by a persisted request guard. Inspect
its eligibility reason and history before recovery; do not delete action files
or use `--force` just because a request is old. An accepted generation whose quota
anchor is unknown is different from a failed generation.

Claude's daemon reconciliation requires a matching accepted receipt and two distinct,
fresh, direct observations of a fixed later quota window, at least 60 seconds apart.
It rejects stale, synthetic, fallback, unknown-identity and moving-clock evidence.
A later session can retire only a session request; a later week can retire either.
For an accepted weekly request sent against an already-active week, recovery can
also use two fresh direct observations with positive weekly usage, at least two
minutes apart. The inferred week must predate the request by more than five
minutes. Deadline rounding is limited to 60 seconds against the original
observation, never a moving reference. This records `existing_weekly_window`;
it does not claim the request started that week. Rounded zero usage can suppress
a redundant weekly request but cannot retire its guard.

The original outcome remains unconfirmed in history. Recovery records its evidence
under the action lock and delays new dispatch by at least one polling interval.
It never generates a replacement request. Malformed state or missing evidence
remains blocked and visible for operator review.

For an explicitly authorized state-only repair, protect the exact guard in a root-only
backup, restore-test its bytes and keep a hash-verified independent off-host copy.
Run the tested reconciliation policy against a temporary copy first. Under the live
account lock, require the original hash and account identity to still match and
revalidate current native evidence before replacing only that guard. Use the app
user for the write; preserve controls, history and credentials. Do not install
unpublished code into the running service. A one-time state repair on an older
version does not deploy the new automatic recovery or status behavior.

Rollback requires explicit authorization: first confirm no later provider action
has occurred, preserve the recovered record, and restore only the original guard
under its lock. Never overwrite a newer request record. Retain both backups and
the recovery audit until the operator accepts the repair.

## Antigravity confirmed-request reconciliation

An enabled stagger can be blocked by two separate failures: a native quota
schema/transport failure and a genuinely uncertain dispatch. Refresh success
alone must not clear a dispatch guard.

When the controller says `unconfirmed` but the corresponding generation history
is already confirmed, the repaired daemon checks the account's completed action
record and durable family cooldown under the account lock. Exactly one matching
confirmed session history event with response and provider-movement evidence is
required. It records `last_request_recovery` and returns without generating;
normal scheduling resumes on a subsequent poll with fresh native observations.
History and cooldown records are preserved. Unknown requests, conflicting
records, missing history and active dispatch ownership remain blocked.

Do not delete guard files, force a kick, or replay missed slots to fix a parser
outage. Confirm that native monitoring reads both ordinary families and keeps
the optional Argon weekly quota separate. A read-only candidate-parser check
does not install the fix into the daemon; deployment is a separate operation.
