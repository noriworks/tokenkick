# Antigravity quota schema and confirmed-request recovery

## Evidence (17 October 2026)

The live native summary contains the original four Gemini/Claude-GPT buckets
plus `gemini-argon-wtu-weekly` in a separate Gemini 4 Argon group. Substring
classification maps this to the ordinary Gemini weekly ID, then rejects the
duplicate. The cache retains only `ProviderError`, hiding the parser diagnosis.

The last Gemini generation is confirmed in history and has a durable five-hour
cooldown, but stagger state calls it unconfirmed. The controller confirms a
request, then makes another monitoring read; its exception handler downgrades
that confirmed request when the read fails. This permanently blocks recurrence.

## Repair and acceptance criteria

1. Parse the observed optional Argon weekly bucket distinctly, retaining the
   four mandatory windows and strict validation of duplicates, unknown IDs and
   malformed evidence. Never infer a five-hour Argon bucket or use its weekly
   quota for Gemini/Claude scheduling. Block Argon overrides of Gemini kicks
   when the native response advertises this separate quota.
2. Retain a safe, fixed diagnostic code/message for native refresh failures in
   cache/status. Do not save arbitrary provider text or credentials. Clear it
   after success; retain the old observation age after failure.
3. Preserve confirmed request outcomes across subsequent monitoring failures.
   Recover legacy downgraded controller attempts only from a uniquely matching
   confirmed history event, the matching durable family cooldown and completed
   action guard under the same action lock. Unknown dispatches stay blocked.
   Reconciliation itself sends nothing; resume on a subsequent daemon tick with
   fresh native observations and all existing controls/cooldowns intact.
4. Regression tests cover the captured schema, isolation, missing/duplicate or
   malformed buckets, cache diagnostics, interrupted monitoring, restart,
   legacy recovery, no evidence/contradictory evidence, and recurring alternation
   without early/duplicate requests. Update the old regression expectations
   that incorrectly require confirmed requests to become uncertain.
5. Verify current provider data using the patched parser in an isolated process,
   and replay the actual persisted controller evidence locally with sending
   forbidden. No unpublished code installation, production-state edits, CTP or
   publication in this batch. Permanent production recovery awaits deployment.

## Verification

- Live read-only candidate-parser check on Hetzner at 12:44:12 CEST, 17 October
  2026: all five windows parsed; both normal families classified as available
  and unanchored; Argon remained a separate weekly window. No generation sent.
- Replayed the actual October 15 Gemini controller/dispatch/cooldown/history
  records in a temporary local state directory. The controller reconciled its
  outcome to confirmed, returned awaiting evidence and never invoked sending.
  Original guard records were unchanged. Production state was not edited.
- 39 new regression cases; one existing four-case regression updated because
  confirmed sends must remain confirmed after later monitoring errors. It still
  blocks genuinely uncertain requests and now checks no immediate resend and
  minimum five-hour spacing after monitoring recovery.
- Full Python suite: 2,195 passed. Ruff and diff whitespace checks passed.
- Pre-release verification used server 1.23.5 without deploying candidate code.
  The subsequently authorized 1.23.6 release includes this repair; production
  recovery must be verified after upgrade, separately from the local replay.
