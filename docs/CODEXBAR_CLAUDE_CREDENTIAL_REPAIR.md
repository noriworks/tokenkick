# Mac CodexBar Claude scope-metadata repair — 2026-10-13

## Diagnosis before changes

CodexBar 0.74.1 was configured with Claude source `oauth` in
`~/.codexbar/config.json`. Its recorded Claude credential profile points to
`~/.claude/.credentials.json`. The file contained `accessToken`, `refreshToken`,
and `expiresAt`, but no `scopes`.

A targeted read of the native `Claude Code-credentials` Keychain item proved
that its access token, refresh token, and expiry matched the file exactly.
The native entry included `user:profile` and `user:inference`. No token values
were printed, placed in this repository, or sent to a third party.

Before repair, a Claude-only CodexBar OAuth usage check returned exit code 3
and the missing-`user:profile` error, without usage data. The installed-version
source explains why: it parses the file as a Claude credential, treats absent
scope metadata as an empty list, and validates that list before requesting usage.
An unexpired token can therefore fail locally despite having the actual permission.

## Recurrence mechanism

The installed Hermes `agent/anthropic_credentials.py` reads Claude credentials
into a normalized token/refresh/expiry record. Its refresh call does not supply
scope metadata to `_write_claude_code_credentials`. That writer preserves scopes
only when they already exist in the JSON file; creating a new file from a native
Keychain credential can omit them, and subsequent refreshes perpetuate the omission.
The Keychain mirror separately preserves native metadata.

This is a concrete recurrence mechanism matching the observed file. We did not
have a filesystem audit identifying the process responsible for its last write.
It is not evidence that the native token lacks permission, and logging in again
on Hetzner cannot repair this Mac's metadata.

Relevant installed-version source:

- [Credential selection and file-change invalidation](https://github.com/steipete/CodexBar/blob/v0.74.1/Sources/CodexBarCore/Providers/Claude/ClaudeOAuth/ClaudeOAuthCredentials.swift)
- [Scope validation before usage requests](https://github.com/steipete/CodexBar/blob/v0.74.1/Sources/CodexBarCore/Providers/Claude/ClaudeUsageFetcher.swift)

## Applied repair and verification

Restored only `claudeAiOauth.scopes` from the matching native Keychain record.
The repair acquired Hermes's existing `.credentials.lock`, checked exact equality
of both tokens and expiry, refused conflicting scope metadata, checked the file
had not changed, and atomically replaced it with mode `0600`.

Both tokens, expiry, and every other file field remained unchanged. No login,
logout, token rotation, revocation, credential-source change, cache purge, or
Hermes installation patch was needed. This restores metadata for permissions
already possessed by the identical credential; it does not request new scopes.

Validation:

- Eight fixture checks covered restoration and rejection of mismatched credentials
  or unproven scopes.
- The actual installed Hermes writer, isolated with fake credentials and stubbed
  persistence/Keychain functions, preserved the restored scope list through five
  successive simulated refreshes.
- The real Claude-only CodexBar OAuth check then returned **exit code 0**, current
  usage data, no missing-scope error, and no reported rate limit.
- No generation request was sent. No credential-bearing output was retained.

The running menu-bar app uses a five-minute refresh interval. Its own system log
confirmed a credentials-file change and cache invalidation at 13:02:55 Europe/Berlin
after the repair. Its UI could not be inspected because the desktop automation
service timed out; the CLI verification and app detection are confirmed, while
a visual menu check is not.

## Follow-up and batch boundary

If the credential file is deleted/recreated or another writer strips scopes,
inspect the source again. Never invent scopes, insert `user:profile` merely to
silence the error, copy metadata across differing tokens, or blindly repeat login.
The long-term upstream Hermes improvement would preserve verified native metadata
when first creating its shared credential file; this task did not publish or patch
Hermes. Existing writer behavior preserves the repaired scope list.

Only this diagnostic note was added to the TokenKick repository in this step.
Earlier implementation and tests remain in the current uncommitted batch.
No CTP, package publication, app release, or server deployment occurred.

## Follow-up: expiry and blocked native renewal, 2026-10-13 evening

The next error was different: “Claude OAuth token expired; background refresh is
disabled by the Keychain prompt policy.” The earlier scope repair remained intact:
the file still had `user:profile` and a refresh token, but its access token had expired.

Before changing settings, inspection established:

- Claude remained configured to use OAuth. Its prompt policy was already `always`.
- The independent global `debugDisableKeychainAccess` preference was true.
- Claude-specific `claudeOAuthDirectKeychainReadAllowed` was unset (default false).
- A targeted, in-memory comparison showed the native `Claude Code-credentials`
  item was unexpired and had `user:profile`; access token, refresh token and expiry
  differed from the expired file. No credential values were printed or retained.

The installed 0.74.1 coordinator returns `skippedByPromptPolicy` when either the
global Keychain block is enabled or the prompt mode forbids background access.
Its user-facing message does not distinguish these causes. After the user
explicitly authorized Keychain access and verification, the global block was
disabled in the app and its current shared preferences, and the app restarted.
The next renewal attempt was no longer policy-blocked, but reported that the CLI
touch did not update the Keychain. The native credential was already fresh;
CodexBar still lacked its separate direct-read permission.

Enabled “Allow reading Claude Code's credentials” under the approved Keychain
repair and restarted CodexBar. Its real app widget snapshot then contained fresh
Claude usage at **20:04:06 Europe/Berlin**: **22% session remaining, 70% weekly
remaining**. This verifies recovery in the running app, not merely a separate CLI.
Desktop UI automation timed out, so the visible menu itself was not inspected.

Final settings:

- Settings → Advanced → Privacy → Disable Keychain access: **off**.
- Claude → Keychain prompt policy: **Always** (already configured, unchanged).
- Claude → Allow reading Claude Code's credentials: **on**.
- Claude usage source: **OAuth**, unchanged.

No manual token copying, login, logout, revocation or generation request was used.
CodexBar may ask for macOS Keychain permission as Claude rewrites its native item;
these settings permit access, not bypass macOS authorization. The next expiry
cycle has not yet been observed, so this is a verified current recovery plus a
repair of the demonstrated blockers, not a guarantee against all future auth errors.
Re-enabling the global block or disabling Claude's direct-read permission restores
the previous access restrictions. No evidence identifies who originally enabled
the global block.

The first repair's verification covered scope preservation and a then-valid token,
not expiry recovery. This follow-up closes that diagnostic gap. Only this note was
updated in the existing unpublished TokenKick batch; no release or server change.
