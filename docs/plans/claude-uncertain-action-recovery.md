# Claude request guards and evidence-only recovery

An accepted Claude request with no quota anchor left a persistent uncertain guard.
Eligibility and the UI ignored it, while dispatch silently rejected it. Fix those
layers together; never clear a guard merely because time passed or a switch is on.

Acceptance:
- Shared read-only guard diagnostics cover eligibility, terminal/JSON/MCP status,
  daemon target selection and send-time race failures for every provider.
- Accepted Claude generations awaiting quota evidence are distinct from transport
  uncertainty. Neither permits a replacement request in the same window.
- Claude recovery requires a matching accepted receipt, fresh direct data and two
  independent observations of the same later native window. A session observation
  cannot retire a weekly request. Validate timestamps, duration, source and identity
  binding; refuse malformed records, stale/cached repeats and concurrent owners.
- Under the same action lock, retain original outcome and recovery evidence. Recovery
  sends no generation, never marks the original request confirmed, and adds a short
  dispatch cooldown so the current daemon pass cannot immediately send another request.
- Unresolvable guards remain explicit. Provider errors cannot silently become permission
  to generate. Tests cover restart, contention, duplicate prevention and UI agreement.
- Preserve notification edits. No CTP, publication or installation. Any authorized
  production state recovery must use a protected tested backup and independent copy,
  exact account/record matching and native evidence; do not force a kick.

Implementation notes: persist acceptance before the interruptible quota read; retain a durable receipt so history pruning cannot strand future requests. A verified action remains crash-protected until its history-writing scope completes. Guard recovery never changes historical confirmation and uses a per-account lock plus a cooldown. Legacy records require a matching history receipt. Tests exercise the real daemon recovery phase and show no dispatch during recovery, one later normal dispatch, then duplicate rejection.

Verification: 43 new regression cases in test_claude_action_recovery.py; no existing tests changed for this repair. Full suite 2149 passed, including the notification batch. Ruff and diff checks passed; focused request-safety tests rerun after final diagnostic wording. Production state-only recovery completed using protected backups, exact guard matching and two native observations; Claude history and controls stayed unchanged and no generation was sent. This pre-release verification used server 1.23.5. The subsequently authorized 1.23.6 release includes the recurring recovery and UI changes.
