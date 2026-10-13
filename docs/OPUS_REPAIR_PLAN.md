# Opus review repair batch

Status: all 55 confirmed findings implemented and locally verified on 2026-10-13; accumulated diff reviewed. No commit, tag, push, publish, deployment, real provider generation, credential changes or notification sends are part of this batch.

## Strengthened execution plan

The consolidated review is the scope authority. Address all 55 numbered confirmed findings, including the confirmed mechanism of mixed C/H findings; retain their unverified production implications as hypotheses. Rejected raw-review findings are not fixes. Preserve the already-unpublished work.

1. **Request safety (A1–A10, B9).** Identity checks must fail closed before generation. Persist a preparation/dispatch distinction, preserve ambiguous-send guards, distinguish safe refusal from an attempted request, verify weekly anchors, bind MCP approvals and contain subprocesses. Acceptance: no mocked generation on mismatch/refusal; no lost pending action on refusal; no automatic repeat after uncertain dispatch; changed previews cannot widen scope.
2. **State and recurring operation (B1–B4, C1–C6).** Transactions must cover the entire pending read/modify/write. Preserve settings and unknown noncritical schema fields; reject malformed controls. Disable must clear group ownership even after identity drift. Refuse cross-window replacement rather than silently migrate the storage model in this batch. Acceptance: concurrent deletion cannot resurrect; broken groups do not abort other work; shutdown drains dispatch; clocks render in the configured timezone.
3. **Observation correctness (B5–B8, F1–F2).** Reconcile source freshness, recover dead locks without stealing live owners, separate latency from errors, support frozen launch commands and stable process identity. Acceptance: newer native data wins; old owners cannot delete new locks; one internal discovery error causes one invocation.
4. **Public interfaces (D1–D12).** Shared mutation invariants and structured effective states across CLI/TUI/MCP. Render external strings literally. Move notification side effects out of rendering. Credential changes revoke old remote authorization. Acceptance: invalid input stays in menus; errors have nonzero exits/structured envelopes; poison updates do not replay endlessly; warnings remain visible.
5. **macOS (E1–E10 and confirmed minor follow-ups).** Immutable action targets; request-generation checks; awaitable refresh generations; structured route decoding; live daemon ownership and transactional install; uncertain outcomes and controller visibility. Acceptance: reverse completion order cannot mix account data; mutation refresh waits for new data; confirmed target survives selection changes; launchd settings match app settings.
6. **Delivery and documentation (F3–F8).** Hermetic fixtures, synthetic identities, bound plan examples, current provider semantics, installable service instructions, failure-safe DMG cleanup and exact-artifact release validation. Acceptance: tools are stubbed in fixture tests; no provider access; packaging failure detaches only its own mount; release metadata and artifact checks are explicit.

## Verification gates

- Add focused regressions before or alongside each coherent repair; exercise negative and adjacent paths, not implementation mirrors.
- Keep provider calls, notifications, launchd/systemd and mutable user state mocked or isolated. No genuine credential material is needed.
- Run affected tests after each stage. At completion run the full Python suite, Ruff, Swift suite, package-resource checks, and a freshly built frozen-runtime smoke test in an isolated state directory.
- Review complete accumulated diff for regressions and preserve all pre-existing changes. Record intentional test expectation changes by filename and reason. Do not weaken checks to get green tests.
- No new dependency, schema-breaking migration, provider retry policy or real quota test is assumed authorized by the review itself. Prefer existing tools and backwards-compatible fields.

## Findings ledger

| ID | Status | Verification / implementation |
|---|---|---|
| A1 | implemented | Positive identity gates before fallback/probe and dispatch; request-safety identity regressions. |
| A2 | implemented | Account-matched CodexBar selection and verified migration candidates; source regressions. |
| A3 | implemented | Claude response acceptance separated from weekly anchor verification; moved/unverified tests. |
| A4 | implemented | Typed not_sent outcome; no failure budget/history/notification; exact pending replacement protection. |
| A5 | implemented | Current/expired weekly-window guard boundaries; recent uncertain request regression. |
| A6 | implemented | MCP run action-set fingerprint, pending/trigger comparison and single consumption; real CLI and scope tests. |
| A7 | implemented | Owned subprocess-group cleanup; timeout outcome_unknown; client budget increased to 240 seconds. |
| A8 | implemented | Explicit reset-prose parsing and native evidence checks; antigravity window tests. |
| A9 | implemented | Recovery compares history after its bookkeeping event; CLI recovery tests. |
| A10 | implemented | Attempt state persists before post-read; fallible reads cannot erase request evidence. |
| B1 | implemented | Whole pending transaction plus compare-and-update completion; cross-process cancellation regression. |
| B2 | implemented | Atomic private backups for config and MCP; hardening tests. |
| B3 | implemented | Policy-preserving migration and identity-key rekeying; migration tests. |
| B4 | implemented | Strict typed safety configuration; unknown optional fields round-trip; schema compatibility checks. |
| B5 | implemented | JSONL observed_at survives parsing; newer native read wins; stale/recent reconciliation tests. |
| B6 | implemented | OS lock for Antigravity; serialized refresh ownership tokens; empty/dead/live/replacement tests. |
| B7 | implemented | Native latency is diagnostic data, not an error; slow-success regression. |
| B8 | implemented | Frozen executable command selection; freshly built detached refresh completed and released lock. |
| B9 | implemented | Preparing vs in-flight/uncertain durable leases; crash and pre-send exception tests. |
| C1 | implemented | Broken group routing tolerates missing members; daemon isolates group exceptions. |
| C2 | implemented | Disable clears ownership by group marker after member identity drift; dedicated regression. |
| C3 | implemented | Independent work windows are explicit conflicts; no silent replacement; diff regression. |
| C4 | implemented | One-second reset rounding tolerance; subsecond and meaningful-drift regressions. |
| C5 | implemented | Plan/advisory timezone carried through displays; UTC-host and existing DST tests. |
| C6 | implemented | Graceful dispatch drain; transient blockers re-evaluate next poll, uncertain sends need recovery. |
| D1 | implemented | Expected Click validation errors stay inside menus; selection/menu regression suites. |
| D2 | implemented | Read-only renderers; operational notification claim persists before send; dedupe regression. |
| D3 | implemented | Literal labels, errors and responses in status/history; malformed Rich-tag regression. |
| D4 | implemented | Shared setter rejects pruning every surface; selection seed preserves saved choices. |
| D5 | implemented | Stored and effective weekly/session state plus master/controller blockers shown separately. |
| D6 | implemented | Finite integer burst gaps and nonzero CLI validation exits; input/exit regressions. |
| D7 | implemented | MCP kick preview declares live provider refresh and appropriate timeout. |
| D8 | implemented | Nonfinite percentages handled; poison update acknowledged; restart offset regression. |
| D9 | implemented | ntfy destination masked in doctor payload/checks and forwarded diagnostics. |
| D10 | implemented | Listener rechecks current credentials before polls and each update; revocation regression. |
| D11 | implemented | Telegram warning accounts precede healthy rows within the display budget. |
| D12 | implemented | MCP recovery/pending resources preserve errors; real CLI error contract coverage. |
| E1 | implemented | Account request generations and loaded-editor identity guard; delayed A/B regression. |
| E2 | implemented | Overlapping refresh awaits queued post-mutation fetch; held-first-fetch regression. |
| E3 | implemented | Structured notification enabled/backends decoding; inherited-route tests. |
| E4 | implemented | LaunchAgent includes Settings PATH and detects drift; repair regression. |
| E5 | implemented | Fresh ownership before install; failed start rolls back files; ownership/rollback tests. |
| E6 | implemented | Timeout/cancellation displays unconfirmed, with reconciliation guidance; outcome regression. |
| E7 | implemented | POSIX/Gregorian CLI clock formatting and explicit timezone; planner tests. |
| E8 | implemented | Confirmation captures schedule scope; selection change before apply regression. |
| E9 | implemented | Acknowledged-at snapshot contract clears warnings; corrected structured fixture regression. |
| E10 | implemented | Controller status visible; daemon warning covers pending/controller work; gave-up/expired next actions excluded. |
| F1 | implemented | Invariant process creation identity; one real process matched under UTC and Asia/Tokyo. |
| F2 | implemented | Removed broad TypeError compatibility retry; one-call failure regression and corrected doubles. |
| F3 | implemented | Hermetic fixture entry point blocks network/processes and substitutes discovery/passwd home; generator passed. |
| F4 | implemented | Synthetic presentation identifiers; dirty/untracked export refused; export tests. |
| F5 | implemented | Bound plan execution examples; provider semantics and retired demo corrected and regenerated. |
| F6 | implemented | Explicit systemd unit generator and installation guide; nonstandard-home/path regression. |
| F7 | implemented | Unique DMG workspace/mount and cleanup trap; injected layout failure detaches only owned mount. |
| F8 | implemented | Exact wheel/sdist/tag metadata and packaged docs gates; release uses verified Hatchling 1.32.4; explicit unsigned-beta signature policy; artifact checks. |

## Test changes and final checks

Intentional changes to existing test contracts (no tests removed or skipped):

- `test_cli.py`: discovery/fetch doubles accept the real keyword arguments after removing the unsafe TypeError retry; synthetic identity fixtures authorize dispatch; Claude acceptance without anchor now expects unconfirmed; dormant rendering expects no notification; invalid schedule/reset-log inputs expect nonzero exits; refresh locks include ownership tokens and unreadable-lock diagnostics identify the read failure; schedule tests use one controlled clock; Berlin display fixture explicitly selects Berlin.
- `test_sources.py`: identity mismatch/unreadable identity cannot fall through to unrelated data; unscoped widget fixtures explicitly omit account scope; archived JSONL cannot replace native status; event observation time retained; positive probe fixtures establish identity.
- `test_kicker.py`: positive synthetic identities are explicit; generation alone is unconfirmed; unsupported pre-dispatch requests do not create attempt history.
- `test_models.py`: unknown scheduling target is rejected instead of silently enabling auto semantics; unknown account fields are preserved.
- `test_model_selection.py`: catalog failure before generation leaves history empty.
- `test_hardening.py`: simulated crash marks dispatch first; successful export uses a committed clean fixture.
- `test_mcp_core.py`: fake CLI includes approved action-set metadata and apply expects that binding.
- `test_antigravity_automation.py`: provider-fetch double accepts real keyword arguments.
- `test_claude_monitoring.py`: an account lacking fallback identity keeps the direct failure context, rather than trusting unscoped fallback data.
- `test_stagger.py`: send doubles include dispatch lifecycle; new identity-drift, missing-member and safe-refusal regressions.
- `test_account_presentation.py`: personal handle replaced by equal-width synthetic account text.
- Swift `AccountsAndNotificationsTests.swift`: route tests use structured settings; consent apply echoes the returned token/version.
- Swift `DaemonViewModelTests.swift`: ownership-safe start owns installation rather than installing before validation.
- Swift `KickTests.swift`: thrown/timeout outcome is unconfirmed rather than failed.
- Swift `LaunchAgentTests.swift`: known ownership supplied; preflight inspection precedes bootstrap; new rollback/PATH tests.
- Swift `PlannerScheduleTests.swift`: change selection before confirming and require the previewed scope.
- Swift `WarningDerivationTests.swift`: acknowledged fixture uses the actual `acknowledged_at` contract.
- Swift `AdvancedTests.swift` and `SnapshotStoreTests.swift`: new controlled asynchronous race regressions.
- Swift `PopoverModelTests.swift`: abandoned/expired actions are excluded from the next-action summary; copied commands preserve quoted labels safely.
- New `test_review_request_safety.py`, `test_review_state.py`, `test_review_integration.py`: isolated negative/positive safety and boundary regressions, including real CLI/MCP subprocesses and concurrent cancellation.
- Final `test_review_state.py` additions reject string-valued optional boolean controls, nonfinite thresholds and null labels, and cover lock truncation between metadata and content reads. The malformed-config assertion compares unchanged file text, since NaN does not compare equal to itself; valid list-valued settings remain covered by the existing configuration round-trip tests.
- New `conftest.py`: isolate all imported runtime-state path constants; existing tests may override their own paths.

Pre-existing test changes in the batch (`test_app_mode.py`, Swift `DecodingTests.swift` and prior parts of the files above) are retained. The saved pre-repair diff distinguishes them from this repair.

Additional confirmed macOS follow-ups are included: core-provided consent token/version, plan error-envelope messages, first-run discovery cancellation, and shell-quoted copied labels. A running older daemon must advertise the required controller/dispatch capabilities before stagger can be enabled; the future release still needs its own version bump.

Final verification:

- Full Python suite: **1,901 passed**, 41.53 seconds.
- Swift suite against the freshly rebuilt bundled runtime: **210 passed, zero failures**, 8.62 seconds.
- Ruff and `git diff --check`: passed.
- Fresh wheel, sdist and PyInstaller runtime built locally. Exact artifact-version checks passed using the unchanged 1.22.0 metadata; no release tag was created.
- Wheel documentation and frozen MCP resource reads over stdio: passed outside the source checkout.
- Frozen runtime background refresh: real detached child updated isolated cache and released its owned lock.
- Hermetic Swift fixture generation: all 13 files validated in a temporary destination, with external processes/network blocked and synthetic discovery/home data.
- Integration regressions exercised actual subprocess-group timeout cleanup and concurrent pending cancellation; packaging failure used stubbed mount/layout tools. Process identity remained stable across UTC and Asia/Tokyo.

Verification is local and isolated. Live provider acceptance/reset anchoring, Hetzner operation, actual launchd/systemd installation, distribution signing and a published release remain outside this batch. No live provider generation, remote notification, server change or release was used for these tests. All changes remain uncommitted in the current batch; the next release requires a version bump and daemon restart.

## Release authorization and checkpoint

The user subsequently authorized CTP, PyPI publication, the server upgrade and
the updated non-notarized macOS beta installer. Release **1.23.0** includes this
repair batch and the preceding unpublished features. Release verification passed:
1,901 Python tests, 210 Swift tests against the rebuilt 1.23.0 runtime, Ruff, diff
checks, exact wheel/sdist metadata, packaged MCP resources, and app/DMG signature
and integrity checks. Earlier no-publishing statements describe the pre-release
verification boundary; they do not supersede this later explicit authorization.
Deployment and protected backup details are kept in the private operator record.

The initial 1.23.0 tag passed macOS verification but Linux CI found two
nonportable test fixtures. No PyPI package was published. The forward release
**1.23.1** corrects `test_cli.py::test_render_plan_uses_compact_human_output` to
construct Berlin timestamps explicitly, and `test_review_integration.py` to run
its real child CLI through `sys.executable -m tokenkick`. Both assertions are
unchanged; no test was skipped or relaxed. The full suite was rerun with `TZ=UTC`.
The 1.23.0 tag remains immutable.

The executable-module regression also exposed an actual installed-runtime bug:
`status_cache.py` launched `tokenkick.cli`, which has no module entry point.
1.23.1 adds `tokenkick/__main__.py` and launches `python -m tokenkick`. The
existing command-construction test now expects that entry point; a new real
background-child test requires a written cache and removal of its owned lock.
