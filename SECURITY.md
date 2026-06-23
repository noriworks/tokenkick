# Security Policy

## Reporting A Vulnerability

Please do not open a public issue for suspected vulnerabilities, leaked secrets,
or account-safety problems.

Report security issues privately through GitHub private vulnerability reporting
on the public repository once it is enabled. If that is unavailable, use the
private contact channel published by the maintainer. Include:

- affected TokenKick version or commit
- operating system and install method
- the command or workflow involved
- a concise reproduction, if safe to share
- any logs or screenshots with account IDs, tokens, homes, and local paths
  redacted

I will acknowledge reports as quickly as practical, investigate, and coordinate
a fix before public disclosure when the issue is confirmed.

## Scope

Security-sensitive areas include:

- leaked or mishandled provider credentials, tokens, or local account state
- accidental exposure of `~/.tokenkick/`, Codex homes, Claude config, or logs
- unintended automatic provider requests
- daemon behavior that acts without an explicit saved setting
- release artifacts or docs that include private account data

TokenKick is a local tool and does not run a hosted service for users in this
release. Provider-account enforcement, quota policy, and Terms of Service
questions should be handled with the relevant provider.
