# Mac CodexBar saved Codex account renewal — 2026-10-13

## Cause

CodexBar 0.74.1's OAuth usage strategy reads native credentials but deliberately
refuses to redeem expired refresh tokens itself. A second writer could rotate a
shared token without safely publishing the new credentials to its owner. Its
Auto/OAuth CLI fallback is disabled when a managed workspace is selected, because
that fallback cannot pass CodexBar's workspace header to the CLI.

On this Mac, the system account uses the system home. the two saved accounts use
separate CodexBar-managed homes. Their access tokens expired on June 4 and June 5,
respectively. Both selected workspace IDs matched their native auth files' default
account IDs, making an explicitly selected home-scoped CLI source appropriate.

## Applied configuration

Used the supported command:

```sh
codexbar config set-source --provider codex --source cli
```

This lets the native Codex CLI own authentication and renewal during normal usage
refreshes. It adds no recurring helper, browser login, custom build, or background
LLM job. System account selection remains `liveSystem`. No provider credentials
were copied, manually edited, or replaced. No generation requests were sent.

Rollback is `codexbar config set-source --provider codex --source auto`.

## Verification and remaining limitation

- `codexbar config validate --json` returned no issues.
- The running app adopted the setting: the system account's saved snapshot now reports
  `codex-cli`, with fresh usage and no error.
- Explicit owner-CLI renewal did not update either old managed credential file.
- Clean-environment native login-status checks recognized both saved logins, but
  live usage calls returned HTTP 401, `unauthorized_unknown`, saying the token
  could not be parsed and sign-in was required.
- CodexBar's own CLI-source multi-account probe reproduced those two failures
  while successfully reading the system account. This is not a successful recovery of
  those accounts, nor proof of a specific refresh-token revocation cause.

The user should reauthenticate the existing the two saved accounts rows
once in Settings → Providers → Codex, then refresh all accounts. The intended
steady-state renewal uses the CLI's native auth lifecycle. A future expiry cycle
for those two accounts has **not** been verified because their current saved
logins could not be renewed. Revoked or invalid refresh credentials can still
require interactive authentication.

No TokenKick runtime code changed for this repair. The existing unpublished batch
remains uncommitted and unpublished.

## Sources

- [CodexBar 0.74.1 provider strategy](https://github.com/steipete/CodexBar/blob/v0.74.1/Sources/CodexBarCore/Providers/Codex/CodexProviderDescriptor.swift)
- [CodexBar account and source documentation](https://github.com/steipete/CodexBar/blob/v0.74.1/docs/codex.md)
- [OpenAI app-server authentication](https://learn.chatgpt.com/docs/app-server#authentication-modes)
