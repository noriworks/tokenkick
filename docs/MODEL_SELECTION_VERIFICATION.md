# Economical model selection — 1.21.8

## Scope and acceptance criteria

- Resolve Codex models per account from a bounded live catalog read immediately
  before generation. Prefer GPT-6 Luna and the lowest supported economical effort.
- Continue with another advertised Luna after retirement; never silently select an
  expensive model or a higher effort. Validate explicit overrides against the catalog.
- Override interactive Claude cost settings for completion kicks, explicit probes,
  and pipe/PTY usage reads without rewriting interactive preferences.
- Choose current Flash Low and Claude Haiku/Sonnet Low labels for Antigravity's
  existing evidence diagnostic. Keep automatic Antigravity kicks disabled.
- Remove retired Spark config entries and model pins without deleting credentials,
  history, or unrelated accounts/settings. Ignore stale Spark discovery data.
- Add regressions and validate packaging before updating the server.

## Verification

- Full Python suite: 1,443 tests passed (48 new regression cases).
- Ruff and `git diff --check`: passed.
- Source distribution and wheel built with the existing uv/Hatchling toolchain.
- Wheel MCP resource validation: passed.
- Live local Codex catalog, without generation: selected `gpt-6-luna`, `low`.
- Live local Antigravity catalog, without generation: selected Gemini 3.8 Flash
  (Low) and Claude Sonnet 5.5 (Low).
- No live completion was sent. Provider acceptance and actual quota anchoring
  still require the post-upgrade smoke test. The Hetzner installation and its
  saved Spark entries are unchanged until the upgraded code loads its config.

## Existing test changes

These changes reflect the requested behavior; no tests were skipped or weakened
as a workaround for failures.

`test_kicker.py`:

- `isolate_kicker_config`: supplies an isolated synthetic live model catalog.
- `test_kick_commands_do_not_request_unverified_provider_default_models` renamed
  to `test_kick_commands_use_economical_models_and_settings`: checks explicit
  economical models, effort, standard tier, and Claude thinking settings.
- `test_codex_spark_bucket_uses_spark_model_by_default` renamed to
  `test_codex_spark_bucket_cannot_kick_after_retirement`: verifies retirement blocks kicks.
- `test_codex_phantom_recovery_model_does_not_affect_normal_kicks`: expects the
  dynamically selected Luna instead of an unspecified provider default.
- `test_kick_account_records_requested_reported_model_and_tokens`: expects the
  explicit Haiku request while retaining reported-model/token assertions.
- `test_kick_account_parses_codex_jsonl_metadata`: expects the resolved Luna in
  request history while retaining provider-reported metadata assertions.

`test_cli.py`:

- `isolate_status_cache`: isolates Codex and Antigravity catalog reads from real CLIs.
- `test_codex_direct_discovery_adds_spark_sibling_when_bucket_exists` renamed to
  `test_codex_direct_discovery_ignores_retired_spark_bucket`: stale provider data
  must not recreate the retired account.
- `test_load_account_status_pairs_adds_spark_for_configured_codex_home` renamed to
  `test_load_account_status_pairs_ignores_retired_spark_for_configured_home`: the
  configured-home discovery path must also ignore the retired bucket.
- `test_antigravity_probe_kick_claude_gpt_json_output`: expects Sonnet Low instead
  of GPT-OSS Medium; all before/after evidence assertions are retained.
- `test_codex_kick_surfaces_json_error_from_stdout`: advertises the synthetic
  rejected model in its catalog so it still tests errors at generation time.
- `test_accounts_list_hide_and_show_manage_visibility`: expects the new automatic
  Luna policy label instead of the misleading provider-default label.

New tests in `test_model_selection.py` cover selection preferences, future model
retirement, advertised efforts, explicit overrides, all four Codex surfaces,
account isolation, repeated catalog reads, fail-closed errors, catalog pagination
and cleanup, Claude environment/probe settings, Antigravity family separation,
and persistent/idempotent Spark migration with credential/history preservation.
Two new CLI tests cover Antigravity catalog failures and actual diagnostic flags.
