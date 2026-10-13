# TokenKick — Opus 5.5 repository review, 13 October 2026

This is a review of the current unpublished batch on top of 1.22.0. It is **not a release approval**. No application fixes, credentials, automation controls or production systems were changed for this review. Keep the existing no-CTP/no-publishing boundary.

## Method and evidence

The user authorized sending scoped source, tests, build configuration and relevant documentation to Anthropic, then authorized eight sequential subsystem reviews after the whole-repository prompt exceeded the input limit. That rejected request used zero model tokens. Credentials, runtime logs, unrelated files and private operational notes were excluded. Identifying email addresses, local home paths and server addresses were redacted.

All production Python and Swift source was included across the packets. New/modified Python tests were included, except the large `test_cli.py`, for which the changes and test inventory were supplied. Unchanged legacy Python tests were represented by inventories; Swift test bodies were included. The snapshot manifest covers 212 files. This is broad repository coverage, **not a claim that Opus read every legacy test body**.

All eight responses were verified as `claude-opus-5-5[1m]`, with no auxiliary model usage. Claude ran with high effort, tools disabled, safe mode and no session persistence. Its findings were then checked against the full local source. Local reproductions used temporary directories and mocked provider/notification calls. They did not send generation requests or notifications. The existing frozen CLI was exercised with `--help` only. A temporary Swift harness linked the existing debug modules and used fixture-only services to reproduce the account-selection, overlapping-refresh and route-inference defects; it made no account mutations.

Evidence labels below:

- **R** — reproduced locally with controlled inputs/mocks; this proves the stated behavior, not that it caused a particular production incident.
- **C** — confirmed by following the current source across packet boundaries; not reproduced end to end.
- **H** — plausible concern whose occurrence or impact needs further evidence.

The original model reports are retained separately. Their “confirmed” labels are Opus's assessment; **this consolidation and the corrections below take precedence**.

## Repair order and acceptance criteria

1. **Account identity and request outcomes.** Require verified identity for account-scoped reads and generation; separate a refusal before dispatch, accepted generation, verified window movement and uncertain outcome. Bind MCP approvals to the approved targets. Never retry an ambiguous send merely because fresh status is available.
2. **Durable state and recurring orchestration.** Make pending-action updates transactional; repair stagger ownership recovery; ensure a blocked group cannot abort unrelated daemon work. Preserve migrations and secure backups. Test daemon/MCP concurrency, interrupted sends and restart recovery.
3. **Provider observations.** Prevent stale JSONL from replacing fresher native data, repair lock ownership/recovery, harden relative reset parsing, and verify the packaged refresh command.
4. **CLI, MCP and notification contracts.** Contain input errors, render external strings literally, remove notification side effects from rendering, propagate structured failures, preserve remote listener progress, and enforce mutation invariants in shared helpers.
5. **macOS and delivery.** Apply the platform/build findings after the core contracts are stable. Run Python and Swift suites plus packaged-runtime smoke tests before any future release.

Each fix should include a regression for its trigger and an adjacent-path check. Release verification must include real-CLI MCP contract tests, not only a fake `tk`. Live quota-consuming checks remain a separately scoped verification step.

## Confirmed request, identity and safety issues

| ID | Evidence | Finding and affected path | Required regression / repair direction |
|---|---|---|---|
| A1 | R/C | With Claude generation probes enabled, a direct identity mismatch can fall through to a generation probe. `_fetch_claude_direct` (`tokenkick/sources.py:1035`) calls the mocked probe after an identity failure. Claude invocation also uses ambient CLI credentials, without an independent send-time identity gate. Probes are opt-in; no claim that this happened on the user's account. | An identity mismatch must block fallback generation and unverified cross-account usage. Check identity again at dispatch. Use separate homes if multi-account generation is supported. |
| A2 | C | Claude's CodexBar fallback is not tied to the requested account even after direct identity mismatch. Codex unreadable-identity paths and identity-repair home selection also trust insufficient evidence. | Require positive identity evidence for the target/source; keep unavailable identity distinct from verified mismatch. Verify a candidate home before adopting it. Existing tests that explicitly expect unsafe fallback need an explained behavior change. |
| A3 | R | A Claude **weekly** kick with a successful mocked CLI response is recorded `confirmed=True` without a post-kick usage read (`cli._run_kick_attempt`, `kicker.py`). This is an additional finding from local validation, not one of Opus's eight execution findings. | A response proves generation, not a weekly anchor. Use distinct confirmation fields and verify the relevant window before claiming an anchor. |
| A4 | R/C | Pre-send refusals are represented as failed attempts. The immediate scheduling path removes a pending kick and reports execution even when `_kick_and_notify` returns false (`cli.py:3993`). | Preserve pending work on safe refusal; do not consume retry budget or emit a “request failed” notification when nothing was sent. Return a typed result. |
| A5 | R | The weekly burst pending-confirmation guard derives the wrong interval from an expired reset: it misses a recent unresolved request and can count an old-cycle request instead (`cli.py:2875`). | Test future/current/expired reset boundaries and guard against duplicate unresolved sends independently of a stale window timestamp. |
| A6 | C | MCP `run` approval binds the generic command, not the account/action set returned by the preview (`mcp_core.py:673–718`). New due accounts can enter the apply operation. | Fingerprint and revalidate the approved action set at execution; fail with a changed-preview result before dispatch. |
| A7 | C | MCP timeouts kill the `tk` process without explicit descendant cleanup and return plain `timeout` (`mcp_core.py:1038`). The request may already have been sent. Existing durable guards reduce duplicate risk, so an automatic duplicate is not established. | Bound lifecycle management and classify quota-operation timeout as uncertain, not safely retryable. Make client timeout exceed subprocess timeout plus cleanup. Killing a child does not prove the provider did not accept it. |
| A8 | R/C | Unknown Antigravity reset prose containing the substring “now” is parsed as zero: both “Reset time unknown” and “Resets in 3d (known issue)” reproduce (`antigravity.py:722`). Eligibility can also accept relative evidence that confirmation later rejects. | Match explicit supported expressions; unknown stays unknown. Align dispatch prerequisites with confirmation evidence. Actual provider emission of those exact strings is unverified. |
| A9 | C | Surface reintroduction snapshots history, appends its management event, then passes the old snapshot into a send-time equality check. It can reject its own recovery attempt (`cli.py`, `_execute_codex_surface_reintroductions`). | Compare the relevant generation state, or take the snapshot after bookkeeping; prove the first valid recovery reaches mocked dispatch once. |
| A10 | C/H | Phantom recovery records attempt state after post-request monitoring. If that read raises after an accepted request, durable attempt accounting may be missing. | Persist dispatch intent/outcome before fallible post-read work; inject exceptions at each boundary and verify retry caps still hold. Production frequency is unknown. |

## Confirmed state and monitoring issues

| ID | Evidence | Finding | Required regression / repair direction |
|---|---|---|---|
| B1 | R | Pending-kick load/edit/save is not one transaction. Interleaving cancellation of B with invalidation of A resurrects B (`cli._invalidate_orchestrated_pending_for_account`, `scheduling.py`). | Lock the full read-modify-write transaction; test cancellation/execution/plan application interleavings. |
| B2 | R | Config backups are mode **0644** under umask 022, although they may contain notification credentials. MCP setup backups use a similar pattern. | Create with 0600 or preserve stricter permissions, atomically. Exposure also depends on directory permissions; actual exposure on Hetzner was not asserted. |
| B3 | R/C | Legacy direct-source migration resets notification opt-out, excluded role and reserve threshold; identity/key changes can orphan pending state. | Preserve all user-owned fields and explicitly migrate identity-keyed records or report unresolved ones. |
| B4 | R | Loading and saving config silently drops unknown fields. Malformed config also bypasses consistent validation: `migrations: null` raises `AttributeError`; zero poll interval loads; string `"false"` enables Telegram remote. | Validate types/ranges before mutation; use schema/version checks and forward-field preservation where safe. Do not silently accept unknown safety-critical enum semantics. Coordinate versions sharing a config. |
| B5 | R | Expired session JSONL can replace fresher native Codex limits (`sources.py`). Reproduction changed fresh native weekly/session values to stale JSONL values. | Carry observation timestamps and provenance through reconciliation; native freshness cannot be inferred solely from percent/window shape. |
| B6 | R | An empty refresh lock aged two hours still reports active. Antigravity's age-based lock stealing lets an old owner delete a new owner's lock. | Use actual ownership tokens/OS locks; recover abandoned owners safely. Never steal a known live operation merely because a timeout elapsed. |
| B7 | R | A successful 10.5-second Codex native read stores a latency message in `error`, affecting downstream error semantics. | Keep diagnostics/latency separate from provider failure and test successful slow reads. |
| B8 | R | Frozen CLI background refresh constructs `tk -m tokenkick.cli refresh-cache`; the existing packaged binary rejects `-m` with exit 2 (`status_cache.py:1003`). | Select a launch command appropriate to Python vs frozen executable; test the built artifact, not only monkeypatched argv. |
| B9 | C | In-flight action leases also cover failures before dispatch, leaving manual recovery necessary even when no request could have been sent. | Persist distinct preparation/dispatch phases. **Retain conservative blocking after an ambiguous dispatch**; do not add blanket expiry. |

## Recurring orchestration and scheduling

| ID | Evidence | Finding | Required regression / repair direction |
|---|---|---|---|
| C1 | R | A stagger group with a missing/changed member raises from blocked-notification routing; the exception can abort the daemon poll for other accounts (`stagger.daemon_step`). | Report the broken group without resolving a nonexistent member; isolate group failures from unrelated work. |
| C2 | R | Disabling after a member's identity/key changes leaves its `stagger_group_id` marker behind, so it still appears owned. | Clear ownership by group identity with a safe recovery path, including missing members and duplicated/ambiguous identities. |
| C3 | R | Applying tomorrow's plan replaces tonight's pending kick for the same account without reporting a conflict (`orchestration.py`). | Support multiple scheduled actions per account with stable action/plan IDs, or explicitly reject/confirm replacement. Preserve approved independent windows. |
| C4 | R | A reset 0.4 seconds after the planned timestamp produces a false “plan compromised” advisory. | Use consistent timestamp precision and a documented observation tolerance; do not hide meaningful drift. |
| C5 | R | Berlin 14:00 displays as 12:00 when the process timezone is UTC, without a timezone label, in plan/advisory formatting. | Carry the selected schedule/plan timezone into every display and fingerprint; test host TZ differences and DST. |
| C6 | C | Graceful stop can interrupt a stagger dispatch into an intentionally uncertain state; arbitrary transient blocks do not have a clear recovery policy. | Drain critical dispatch during graceful shutdown, then classify recoverable monitoring blockers separately from uncertain requests. Do not infer safe retry from process termination. |

The persistent controller needs C1/C2 addressed before shipping. One-shot planning and ongoing orchestration should stay explicit, separate modes. Show the effective controller, enabled/paused/blocked state, next due action, target gap and observed gap. A universal stop must still stop owned accounts; do **not** implement Opus's suggestion by silently excluding them from “Disable all.”

## Terminal, MCP and notifications

| ID | Evidence | Finding | Required regression / repair direction |
|---|---|---|---|
| D1 | R | A `ClickException` escapes `interactive._invoke` and terminates the menu. | Contain expected validation errors, preserve cancel behavior, and re-prompt without swallowing unexpected bugs silently. |
| D2 | R | Status rendering calls the dormant-account notifier. Repeated/parallel rendering or dedupe-write failure can repeat notifications. | Keep rendering read-only; emit/dedupe notifications in a transactional operational path. |
| D3 | R | A label, provider response or error containing an unmatched Rich closing tag crashes verbose history with `MarkupError`. | Render external content as literal `Text`/escaped strings, including status, notifications and history. |
| D4 | C | TUI force-prune/keep settings bypass the CLI's “cannot prune every surface” check; shared setter checks overlap only. | Put invariants in the shared mutation layer. Seed the picker with existing selections, not the default surface universe. |
| D5 | C | Weekly/session enable can leave the master off while the success text looks active. The TUI does not clearly distinguish stagger ownership from individual auto-kick controls. | Report stored preference **and effective behavior** with the blocking reason. Do not silently change the master merely to simplify messaging. |
| D6 | C | Burst-gap decimal display and saved integer disagree; non-finite values can raise. Several human CLI error paths exit successfully. | Validate finite/ranged numeric input once; unify displayed/saved precision; make script-visible exit codes match failure. |
| D7 | C | MCP kick preview is labeled cached/no refresh with a 20-second timeout but calls a live CLI status path. | Correct risk/refresh metadata and budget, or implement a genuinely cached preview. |
| D8 | R/C | Non-finite Telegram usage raises; listener acknowledges the update only after handling, so an exception can replay a poison update after restart. | Validate payloads and contain per-update failures with an explicit acknowledgment policy; do not drop all updates indiscriminately. Test two restarts with one bad `/refresh`. |
| D9 | R | Doctor exposes the full ntfy topic in structured output and check text; MCP forwards it. | Mask bearer-like destinations in diagnostics/agent context. Preserve explicit user-facing destination views where appropriate rather than blanket-redacting every UI field. |
| D10 | C | A running Telegram remote binds token/chat once; changing config can leave the old chat authorized until restart. | Detect credential/config generation changes and restart or reload safely; make revocation effective and visible. |
| D11 | R | Twelve healthy Telegram rows consume the display budget and hide the warning account, while “Warnings: 1” remains. | Reserve room for actionable warnings and summarize omitted healthy accounts. |
| D12 | R/C | MCP recovery hints return `ok: true` after doctor failure. Pending-kicks resource similarly collapses snapshot failure to an empty list. | Propagate the structured error; never imply “nothing pending” or suggest force recovery when diagnostics failed. |

## macOS application

| ID | Evidence | Finding | Required regression / repair direction |
|---|---|---|---|
| E1 | R/C | `AdvancedViewModel.loadSelectedCodexDetails` assigns results after awaiting without checking that the selected account is still the request's account. A slow A response can overwrite B's editor; saves use B's current label. | Bind editor data and saves to account identity plus a request generation. Test A/B completion in both orders and disable saving while details are unresolved. |
| E2 | R/C | `SnapshotStore.refresh` returns immediately when another refresh is running. A post-mutation caller can therefore complete before receiving post-mutation data, then an older in-flight response wins. | Queue/coalesce a follow-up refresh and let callers await the required generation; test a blocked first fetch spanning a mutation. |
| E3 | R/C | Notification route editing infers stored configuration from a human display string. An inherited ntfy route looks pinned; global suppression can look like an account opt-out. | Use `notifications_enabled` and structured `backends` (`null` means inherit), separately from effective delivery status. Test inherited-to-pinned changes. |
| E4 | C | LaunchAgent PATH ignores the extra search paths configured in Settings; repair detection ignores PATH drift. | Pass the same environment configuration to foreground and login daemon paths; test plist generation and repair after a settings change. |
| E5 | C | Enabling the login item installs files before checking daemon ownership, using possibly stale/missing snapshot data. Failure can leave the login item installed and its toggle on. | Refresh ownership first and make installation/start transactional. Python's foreground owner lock prevents the alleged duplicate-daemon execution in the ordinary same-config case; leftover configuration is still a real defect. |
| E6 | C | A timed-out/cancelled kick is shown as failed, although the provider may have accepted it. | Present an uncertain outcome and reconcile by attempt/history. Do not encourage immediate retry or remove existing durable guards. |
| E7 | C | Planner command date/time formatting uses current locale/calendar instead of a stable machine format (`PlannerViewModel.swift:197–212`). | Pin POSIX locale/Gregorian calendar for CLI arguments, with an explicit timezone. Test non-Gregorian and non-Latin-digit settings separately from localized display. |
| E8 | C | Schedule confirmation executes using the current `selectedScope`, not the scope captured in its preview (`ScheduleViewModel.swift:261–271`). | Store immutable action arguments/target with the confirmation; test selection changes before apply, including multiple scenes. |
| E9 | C | Acknowledged reset warnings remain: Swift checks an `acknowledged` boolean, while the snapshot sends `acknowledged_at` and includes acknowledged events (`WarningModel.swift:204`; `app_commands.py:183`; `reset_defense.py:433`). | Decode the actual structured contract and test acknowledge → refreshed snapshot → warning cleared. This promotes raw review H1 from hypothesis to a confirmed cross-language mismatch. |
| E10 | C | Recurring stagger is decoded but not surfaced; daemon-down warnings consider individual auto-kick flags but miss pending plans/controller work. Popover next-action selection can include gave-up entries. | Show effective ownership/controller state and warn whenever actionable scheduled/controller work requires a stopped daemon; exclude abandoned work from “kick now.” |

Other macOS follow-ups: capture consent version/token from the core rather than hard-coding it; handle plan error envelopes; make first-run “Not Now” explicitly cancel or continue in the background; ensure copyable shell commands quote labels. These are lower-priority than account-target and refresh races. No live launchd changes or real account writes were used to validate this review.

## Build, fixtures and documentation

| ID | Evidence | Finding | Required regression / repair direction |
|---|---|---|---|
| F1 | R | The same live macOS process has different `process_identity` values when observed under UTC and Asia/Tokyo. The implementation uses human-readable `ps lstart` output (`versioning.py:86`). This can make an app/login daemon appear mismatched to a terminal running in another timezone. | Use invariant OS start identity or normalize locale/timezone. Test two observer environments against one process. The timezone case was reproduced outside the sandbox; locale variation remains untested. |
| F2 | R | Discovery catches any `TypeError` and re-runs without config; an injected internal error caused two calls and stripped arguments (`discovery.py:79,158`). | Remove exception-based signature compatibility; fix test doubles or inspect signatures before calling. Do not retry a partially completed live discovery because a parser failed. |
| F3 | C/H | The fixture generator isolates HOME/PATH but discovery also searches the passwd home and known binary locations. It runs live setup and a non-dry-run kick, while scrubbing paths but not identities. | Stub/deny provider executables explicitly, disallow unexpected network/subprocess activity and validate synthetic identities. The generator was **not** run against real credentials to demonstrate leakage. |
| F4 | C | A screenshot regression test contains a personal account handle. Public source export copies tracked working-tree files, and its short omission list is not a privacy boundary. | Replace personal fixture data before release and add scoped fixture/export hygiene checks. Also reject inconsistent dirty/untracked exports or export a verified commit. No export/publication was performed. |
| F5 | C | Agent examples omit plan preview binding; the CLI permits omission and binds a plan computed at apply time instead (`cli.py:7592`). Old docs disagree about Antigravity kick support, Claude session behavior, retired Spark and model defaults. | Update every execution example to carry `expected-plan`/`preview-at`, and reconcile provider documentation with actual behavior. Consider requiring binding for noninteractive approved-plan apply. |
| F6 | C | Shipped systemd units hard-code `/usr/local/bin/tk` and `/home/%i`, which do not necessarily match the documented per-user pipx install. | Provide explicit path/home installation substitution or documented drop-ins; test the published installation procedure. Do not assume these units match the user's customized live service. |
| F7 | C | DMG layout failure after mount has no cleanup trap; subsequent runs reuse and recursively remove the fixed mount directory. | Use a unique owned mountpoint, trap detach on failure, and make Finder layout optional. Verify failure cleanup with stub tools, not a real destructive rerun. |
| F8 | C | Release CI rebuilds the distribution on a different Python job and publishes that artifact without repeating package-resource verification; no tag/version equality gate is shown. DMG signature verification ignores failures. | Verify the exact artifact published; assert tag/version equality. Distinguish unsigned beta verification from a distribution-signed release policy. Pin/reproduce build inputs through the existing dependency workflow. |

Before future deployment, require a distinct release version and a runtime capability handshake so an older daemon cannot ignore newly written controller ownership. Keeping this unpublished batch at 1.22.0 is not itself a current deployment defect. Potential dual systemd/internal supervision, false global-reset detection from sliding clocks, and hardened-runtime entitlement issues remain hypotheses requiring focused tests. Test-fixture isolation should cover every imported state-path constant, not just `models.CONFIG_DIR`.

## Corrections to the raw reports

- macOS D1’s proposed background-daemon kill is **rejected for current Python launch paths**: daemon, status-refresh and Telegram children all use `start_new_session=True`. Do not weaken intentional owned-descendant cleanup on this basis. macOS D3’s duplicate-daemon impact is narrowed by the core foreground owner lock.
- Terminal finding 6 (stale JSON vs eligibility) is **rejected**: `_status_rows_as_dict` calls `effective_cached_status` before eligibility, and that helper applies `refresh_error` to the status.
- `auto ... --scope all` already sets master, weekly and session. The raw terminal hypothesis to the contrary is rejected. The master-off wording problem for *individual scopes* remains.
- The claimed general MCP/Swift stagger error-envelope gap is narrowed: `TK_APP_MODE` has a top-level exception envelope. Raw `--json-output` without app mode still deserves consistency checks.
- Proposed lock-order deadlock was not supported by the actual ordering/reentrant locks. Proposed generic notification fan-out abort is largely covered by per-backend exception handling.
- An exception leaving an uncertain-send guard is often intentional protection. Fresh usage alone is insufficient grounds to clear it or retry. Fix pre-dispatch classification and explicit recovery, not the protection.
- Manual kicks can intentionally override scheduling. They should be reflected in/reconciled by orchestration; they are not automatically an authorization bug.
- Missing `user:profile`, OAuth behavior and sliding provider reset semantics were not re-diagnosed through live credentials in this review. Local bugs found here do not prove the cause of prior incidents.

## Improvements beyond individual bugs

- A typed observation model: provider/source identity, observed-at, freshness, window duration and capability, authentication vs rate-limit failure, and evidence of anchor movement.
- A typed request lifecycle: rejected before dispatch, dispatched, provider accepted, response received, window verified, uncertain. Keep trigger (`weekly`/`session`) separate from the actual minimal request.
- One transactional mutation layer for CLI, TUI, macOS and MCP, with stable IDs, preview fingerprints and durable idempotency keys.
- One notification router with per-channel delivery results, consistent secret handling and delivery-aware dedupe.
- Controller status that explains **who will act, when, why or why not**, and clearly separates a one-time plan, a daily availability schedule and a persistent stagger policy.
- Verification at real boundaries: packaged binary refresh, real Click argument trees, macOS command decoding and interrupted daemon/MCP execution. Unit counts alone do not cover these contracts.

## Review limitations

This is not a formal security audit or proof that all bugs are found. Opus performed static analysis without tools. Local checks reproduce selected high-value issues; cross-process, remote provider and live macOS behavior still need targeted integration tests during repair. No application regression tests were changed during this review, and the full Python/Swift suites were not rerun solely for review documentation.

## Artifacts and verification record

- [State/config review](01-state-config.review.md)
- [Provider monitoring review](02-provider-monitoring.review.md)
- [Request execution review](03-kick-execution.review.md)
- [Orchestration review](04-orchestration.review.md)
- [Terminal UX review](05-terminal-ux.review.md)
- [MCP/notifications review](06-mcp-notifications.review.md)
- [macOS review](07-macos.review.md)
- [Build/docs/tests review](08-build-docs-tests.review.md)
- [Local validation results](local-validation.json)
- [Verified model usage](model-verification.json)
- [Source snapshot hashes](snapshot-manifest.json)

Final snapshot comparison: all 212 reviewed files still match their pre-review hashes. `git diff --check` passed. Only this review-artifact directory was added; existing source/test modifications belong to the ongoing batch and were preserved. No commit, tag, push, package publication or deployment was performed.
