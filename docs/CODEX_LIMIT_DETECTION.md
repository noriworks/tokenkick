# Codex plan and quota-window detection

## Plan and acceptance criteria

1. Read `planType` from the selected native usage bucket during the existing
   quota read. Keep it separate from the user-owned planning `plan_tier`; do not
   infer subscription from email, usage percentage, or model access. Preserve
   unfamiliar plan names as provider metadata rather than assuming their limits.
2. Classify valid windows by duration: 300 minutes is the five-hour session,
   10080 minutes is weekly, regardless of primary/secondary ordering. Apply the
   same classifier to native RPC and session-log payloads. Preserve unsupported
   durations for diagnostics; duplicate, malformed, missing, and novel windows
   cannot establish absence. Null is distinct from missing. A complete response
   containing one weekly window and an explicit null companion establishes no
   five-hour window; plan names alone do not.
3. Track detected session capability as present/absent/unknown. Valid independent
   windows remain usable, but unknown windows cannot authorize session kicks.
   Old positional cache entries with a seven-day session require refresh.
   Stale reads and historical session logs cannot establish current absence.
   Fresh provider absence must never be overridden by older session-log data.
4. Add a user-owned per-account override: auto (default), present, absent.
   Expose it through CLI and the interactive Accounts menu, with selected-account
   confirmation. Preserve it through rediscovery. Show detected plan/capability,
   effective behavior, evidence source, override and conflicts in read-only JSON
   and diagnostics. The present override never fabricates usage/reset data;
   absent suppresses session actions without erasing weekly tracking or changing
   the user's stored automation switches. Returning to auto uses retained raw
   observations immediately, not a previously override-filtered cache.
5. Apply the same policy to live/cache displays, session eligibility, phantom
   recovery, orchestration and final request preflight. Recheck current config
   and live native quota before automatic Codex sends, including queued requests;
   an old queued session action cannot turn into an unintended weekly kick.
6. Regressions cover real Pro/Pro Lite/Plus shapes, swapped slots, unknown plans,
   malformed/duplicate/unknown-duration windows, missing vs null, plan changes,
   stale/legacy cache, fallback precedence, override roundtrips/persistence,
   CLI/menu/JSON presentation, weekly eligibility and session/queued safeguards.
   Run the complete Python suite and lint; use a read-only live metadata check
   for the real account shapes. No provider generation, release, deployment,
   version bump, commit, tag or push in this batch.

## Evidence (2026-10-13)

Official pricing currently says Pro has no five-hour limit:
https://learn.chatgpt.com/docs/pricing#what-are-the-usage-limits-for-my-plan
The native app-server documents `planType`, nullable windows, and duration:
https://learn.chatgpt.com/docs/app-server#6-rate-limits-chatgpt

Read-only checks of the configured accounts returned `pro` and `prolite` with
primary=10080 minutes/secondary=null, and `plus` with primary=300 and
secondary=10080. The old parser incorrectly mapped the Pro weekly window to a
seven-day session. No credentials or prompts are needed for the detection.

## Implementation and verification

Implemented the criteria above across native/session-log parsing, configuration,
cache migration, CLI/TUI/JSON presentation, eligibility, orchestration, phantom
recovery, queued actions and final send-time checks. The parser retains the
existing compatibility rule for historical JSONL weekly windows rounded to
10079 minutes; native unsupported durations remain unknown. Manual policy is
applied to copies of observations so toggling an override never erases raw data.
Automatic direct Codex requests require a fresh native response, bypassing the
ambient polling cache; historical fallback cannot authorize generation.

Verification on 2026-10-13:

- Complete Python suite: **1,586 passed**, including 56 new Codex-limit cases
  and the preceding batch's 18 account-presentation cases.
- Repository-wide Ruff checks and `git diff --check` passed.
- macOS Swift suite against the development CLI: **202 passed**, no failures
  or skips. The distributable application was not rebuilt.
- Read-only live quota responses parsed by the updated local implementation:
  `pro` and `prolite` each produced one weekly window and no session window;
  `plus` produced both weekly and five-hour windows. No generation was issued.

### Existing-test changes

No tests were deleted or weakened. New regression coverage is in
`test_codex_limits.py`; the prior presentation batch uses
`test_account_presentation.py`. Existing tests changed only as follows:

- `test_models.py`: `_merge_existing_account` and
  `_merge_discovered_account_fixture` now supply contrasting override values
  (`absent` and `auto`) so the exhaustive user-owned field preservation test
  also checks the new setting.
- `test_cli.py`:
  `test_multi_codex_auto_kick_kicks_all_eligible_accounts_with_each_codex_home`,
  `test_multi_codex_kick_exception_on_one_account_does_not_abort_others`, and
  `test_same_provider_home_main_and_spark_due_kicks_are_serialized` now stub the
  required fresh native read and assert that both accounts were checked.
  Their original kicking, exception-isolation and serialization assertions are
  retained. The historical Spark serialization fixture remains in place.

All changes remain uncommitted for the requested release batch. No version
metadata, installed app, server configuration or release was changed.
