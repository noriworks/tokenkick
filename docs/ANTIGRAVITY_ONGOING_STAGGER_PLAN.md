# Ongoing Antigravity stagger: reviewed implementation plan

Status: implemented locally, unpublished, 2026-10-13. The reviewed design below
is followed by implementation and verification notes. Live activation and release
remain separate.

## Findings

- `orchestration.build_orchestration_plan` plans a finite work window. Roles and
  expected usage guide coverage; they do not enable continuous orchestration.
- `antigravity_stagger.run_once` is deliberately a one-time second-family kick.
- The daemon already refreshes status, executes pending actions and handles
  independent auto-kicks. Recurrence belongs there, on Hetzner, rather than in a
  second Mac/Hermes scheduler.
- Pending kicks currently require account master and relevant window switches,
  expire with their work window, and have retry semantics. Reusing that schema
  unchanged would conflate coverage scheduling with a persistent stagger and
  could retry an uncertain generation. Use dedicated versioned group state,
  sharing the existing guarded execution path and locking primitives.
- `_daemon_sleep_seconds` currently considers the polling interval and recovery
  delays, not a persistent stagger deadline. A ten-minute poll alone cannot
  maintain a precise 150-minute offset.
- CLI and interactive roles tables duplicate presentation and show `Auto/session`
  without controller ownership. Centralize the new presentation contract.

## Intended behavior

Maintain a target phase difference of 150 minutes between the Gemini and
Claude/GPT 300-minute session windows for one verified Antigravity identity.
Adopt already active, verified windows instead of sending setup requests.
This is best-effort timing, not guaranteed uninterrupted capacity: user activity,
weekly exhaustion, unavailable monitoring, provider latency, and outages can
prevent the target offset.

The normal healthy sequence is Gemini at T, Claude/GPT at T+150m, Gemini at
T+300m, Claude/GPT at T+450m. Derive each action from native window evidence;
never repeatedly fire a timer without checking whether the account is eligible.
The start time is the confirmed native reset minus the verified window duration,
not request dispatch time. Report target and observed offset separately.

## Configuration and ownership

1. Add an explicit persistent stagger group: stable member keys, expected shared
   provider identity, family ordering, 150-minute target, enabled/paused state,
   and configuration revision. Initially support exactly one Gemini and one
   Claude/GPT family with verified 300-minute windows. Reject duplicate membership,
   ambiguous identity, same-family pairs, or incompatible window durations.
2. Keep the master automation switch as a genuine stop for all automatic sends.
   Explicit activation enables the required master/session permissions and assigns
   session ownership to the stagger group. Do not represent active orchestration
   as globally disabled. Preserve previous independent settings in the activation
   preview; pausing/disabling must never silently reactivate independent kicks.
3. Every automatic path must honor group ownership, including weekly kicks,
   pending work-window plans and other recovery paths. A weekly action can also
   start a session, so it must wait for an allowed group slot and be coalesced
   with that request. Conflicting schedules block activation until explicitly
   resolved; show their precise changes in the preview.
4. Manual requests remain possible through normal eligibility checks. Warn that
   they may alter the stagger, then observe and reconcile the actual anchors.
   Never force a request or reset an active window to recover alignment.
5. Expose status, preview/apply, pause/resume and disable in CLI, menus and MCP.
   Reuse existing consent/quota acknowledgements and one-use preview tokens;
   bind apply to group configuration revision and exact members/settings. Reads
   are non-mutating. Pause cancels only group-owned pending work and prevents
   further sends; it cannot cancel a request already accepted by the provider.

## Durable daemon state machine

Persist independently of kick-history rotation: confirmed family anchors and
their evidence, target phase, next action/time, attempt identity, config revision,
last outcome and blocker. Write atomically under a group/account locking protocol.
Store UTC timestamps; use monotonic time for in-process sleeps and local time only
for presentation. Corrupt/unknown-version state blocks sends with a clear error.

States: awaiting evidence, scheduled, attempting, verifying, aligned, realigning,
blocked and paused. Before every send, reload enabled state and identity, perform
fresh native checks, recheck quota and pending conflicts, and persist the attempt
before invoking the existing normal kick path. Deduplicate by group/member/window
and retain the existing durable Antigravity attempt guard.

Confirm using independent native reads with stable absolute deadlines. Sliding
idle clocks, relative estimates, one-process cached responses, and zero usage are
not sufficient anchor evidence. After a crash or uncertain response, perform
read-only reconciliation; never automatically repeat an ambiguous generation.
An unresolved attempt requires explicit recovery.

Initial/recovery rules:

- Both active: adopt their anchors. If aligned within tolerance, schedule their
  next eligible slots. If not, show realigning and wait for a safe future slot.
- One active: derive the other family's next 150-minute phase slot from that
  anchor. Preserve any newly observed user-started window.
- Both verified idle: when enabled, start the selected first family once, confirm
  its anchor, then schedule the other family 150 minutes after the observed start.
- Lost timing, overdue action or clock jump: reconcile both families before
  deciding. Do not replay missed actions or send two catch-up kicks together.
- Weekly exhaustion/reserve reached: withhold that family's request and report
  degraded operation; the healthy family may continue at most once per verified
  five-hour window. On recovery, choose a future compatible phase slot.
- Unknown identity, authentication failure, stale evidence, changed window length
  or unresolved generation: block the affected operation and explain why.

Use a fixed target phase during healthy operation, measuring drift against actual
anchors. Never send before native eligibility. Propose a two-minute alignment
tolerance; if lateness pushes a window beyond that band, explicitly enter
realigning and calculate future slots against the surviving confirmed anchor.
Re-alignment may deliberately leave one family idle longer; show that wait.
Do not silently redefine a drifting pair as aligned.

Integrate the next deadline into the daemon wake calculation, with a bounded
sleep so disable/config changes are noticed. Monitoring can retain its ordinary
poll interval; a due action receives fresh targeted checks. No new service or
recurring LLM job is needed. Notifications cover initial alignment and actionable
state changes/recovery, with deduplication; healthy cycles stay quiet by default.

## Terminal and API presentation

In account/status views distinguish:

- Automation: off, independent, stagger-managed, paused, or blocked.
- Controller: group name and family role, or independent auto-kick.
- Next action: family, local timestamp/countdown, or specific waiting reason.
- Timing: target 2h30m, observed offset and alignment state, with freshness.
- Runtime: daemon running/stopped and last successful controller evaluation.

Replace `Auto/session: no/no` with readable ownership-aware text. Put group details
in a compact separate panel rather than adding more columns to already wide
tables. Example (illustrative, not a live status):

```text
Antigravity stagger   Enabled · Aligned · Server daemon running
Target offset        2h30m    Observed 2h30m
Gemini               Managed · next eligible kick 18:52
Claude/GPT           Managed · next eligible kick 21:22
```

Rename the existing roles screen to `Work-window planning roles` and explain
that setting a role is a planning preference, not activation. Add a separate
`Ongoing stagger` menu with status, preview enable/change, pause/resume and disable.
Reuse the same structured status payload in CLI, menus, JSON/MCP and the macOS
app contract, including owner, enabled, operational state, reason and timestamps.
Do not label planned/unconfirmed timing as established alignment.

## Implementation sequence and acceptance tests

1. Pure scheduling/state transitions with simulated time and explicit observations.
2. Versioned configuration/state, locking, revision checks and ownership arbitration.
3. Daemon integration and guarded confirmation/recovery; deadline-aware wakeups.
4. CLI/menu/MCP controls, shared presentation, documentation and app compatibility.
5. Full Python/Swift suites and lint, then a deterministic multi-cycle integration
   simulation. Keep implementation in the current unpublished batch.

Regression acceptance:

- At least three complete cycles retain the target offset within tolerance with
  no early, duplicate or unnecessary generation and correct per-family spacing.
- Adoption of the existing stagger requires no kick. User-started windows are
  preserved; misalignment is visible and eventual safe re-alignment is tested.
- Restart before/after dispatch, crash before confirmation persistence, concurrent
  manual/daemon calls and two daemon processes never duplicate an uncertain send.
- Weekly readiness, independent auto-kicks and work-window pending actions cannot
  bypass stagger ownership or the master stop.
- Exhaustion, configured reserve, account rename/replacement, stale/rolling/malformed
  clocks, missing CLI, auth/rate-limit errors and provider-duration changes fail
  safely and recover only with sufficient evidence.
- Pause/disable during preflight, conflicting configuration edits and stale MCP
  previews cannot authorize a send. No old independent setting silently resumes.
- Due-time wakeups, slow provider calls, DST, wall-clock jumps, late restart and
  missed cycles preserve guards without busy loops or catch-up bursts.
- Narrow terminal views and JSON/MCP consistently distinguish configured ownership
  from actual operation and confirmed alignment from target timing.

Live enablement/deployment is separate from this review and from local simulated
verification. Do not resume the old Hermes controller or modify existing controls
as part of implementing this proposal.

## Implementation (unpublished)

Implemented in `tokenkick/stagger.py` and `stagger_commands.py`, integrated with
normal guarded sends, finite-plan exclusion, pending-action exclusion and daemon
deadline wakeups. `stagger.json` holds versioned policy and runtime state;
`AccountConfig.stagger_group_id` survives rediscovery and prevents a missing state
file from enabling independent requests. Slow preflight reads release the control
lock; final execution checks the policy revision, current permissions and native
evidence. Uncertain attempts remain durable across restarts and history rotation.

The CLI/menu and three MCP tools share the exact CLI mutation surface. Preview
fingerprints bind identity, controls, competing schedules and policy revision;
MCP tokens are one-use and require explicit recurring quota-use acknowledgement.
No ongoing agent task is needed. The existing work-window planner skips owned
members. Status and account views expose ownership, and the macOS snapshot gains
an optional additive `stagger` field without breaking old-runtime decoding.

Additional hardening from implementation review:

- Earliest native retry deadlines are respected even when local attempt time is
  slightly later than the provider anchor; this avoids a premature cycle attempt.
- An interrupted policy/permission update leaves a paused owner.
- Pause/disable remains available if a member is removed. Explicit disable can
  also clear ownership after a lost policy file.
- Explicit recovery inspects fresh native evidence after a full five-hour cooldown,
  clears only the affected action guard, and leaves the group paused. It does not
  claim attribution to an uncertain request or send a replacement generation.
- Notifications deduplicate group state changes across family routes/backends;
  routine aligned cycles are silent.

Regression coverage is in `test_stagger.py`: recurring multi-cycle simulations,
real normal-executor guards, existing-anchor adoption, native cooldowns, early/late
and clock/DST behavior, manual starts, weekly reserves/exhaustion, missing/corrupt
state, interrupted saves, concurrent daemon evaluation, permission/pause races,
MCP registration/serialized calls and real CLI preview/apply, one-use/stale tokens,
notification routing, and deadline wakeups that do not poll unrelated providers.

Existing-test edits in this implementation:

- `test_cli.py`: update the exact empty-status schema assertion for the additive
  stagger object and the Configure menu assertion for the new stagger entry.
- `test_models.py`: add a contrasting saved ownership marker to the exhaustive
  rediscovery-preservation fixture.
- Swift `DecodingTests.swift`: add a test that old snapshots still decode and new
  snapshots retain the optional stagger payload. Existing assertions are unchanged.

The initial incremental Swift build crashed in the native test runner. Focused
snapshot decoding passed; a fresh build with `--scratch-path` passed all 203 tests.
For this model-layout change, use a fresh build directory when reproducing the
full suite rather than trusting the old incremental artifacts.

No live controls or credentials changed, no generation requests were sent, and
nothing was committed, tagged, pushed, deployed or published. Production activation
requires the updated runtime and an explicit enable/resume preview/apply.

Final verification: **1,831 Python tests passed**, including **48 new stagger
regression cases**; **203 Swift tests passed** using the clean build directory
and development CLI. Repository-wide Ruff and `git diff --check` passed. The
registered MCP transport's preview/apply flow was exercised against the real CLI
in isolated temporary state, followed by simulated recurring daemon cycles with
no additional agent calls. Native provider requests and live deployment were not
part of verification. Persisted-state validation is also checked under Python
optimization (`-O`), so safety checks do not depend on assertions being enabled.
