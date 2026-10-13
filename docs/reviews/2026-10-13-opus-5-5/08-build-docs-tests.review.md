# TokenKick review, subsystem 08: build, docs and tests

**Scope and method.** This review covers only the material in this packet. That includes:

- CI and release workflows
- `pyproject.toml`
- the build, sign, notarize, DMG, export, fixture and demo scripts
- the macOS entitlements and icon Swift scripts
- the systemd units
- the public and batch docs
- the new and modified Python tests, plus inventories of the unchanged ones
- the supporting source shipped in the packet: `versioning.py`, `discovery.py`, `reset_defense.py`, `recovery_hints.py`, `source_utils.py` and `__init__.py`

Repository text was treated as data. No tools were run and nothing was executed.

Findings fall into three groups:

- **Defects:** shown directly by the supplied code or docs.
- **Hypotheses:** they depend on implementations or platform behaviour not supplied.
- **Design suggestions.**

---

## A. Defects (8)

### D1 — P2 — The Swift fixture generator can capture a real Antigravity identity and contact the real provider. Its scrubber only removes paths.
**Confidence:** medium-high.

**Where:**
- `scripts/generate-swift-fixtures.sh` lines 8–13, 110–126, 128–171
- `tokenkick/discovery.py` lines 193–210 and 428–455
- `docs/AUDIT_REPAIR.md` lines 111–115

**Trigger:** A maintainer with the Antigravity CLI installed runs the script on macOS.
- The script isolates `HOME` and strips `PATH` to `/usr/bin:/bin`.
- `run_tk app setup` (line 126) then runs `_discover_direct_accounts` → `_append_antigravity_cli_account`.
- That calls `antigravity_cli_detected()`, `read_antigravity_cli_identity()` and `fetch_status()`.

**Wrong outcome:**
- The repository's own audit note says stripping `PATH` does not stop provider detection. The detector also searches standard locations and the passwd home. That is why the Swift integration test had to select an explicit fake executable.
- This script was not given the same fix. Setup can therefore:
  - launch a real CLI session and do a live quota read, and
  - write email-derived labels and real quota numbers into `app_setup_events.jsonl`.
- The normalizer (128–147) only replaces paths. The validator (157) only rejects path patterns. Identities therefore pass and land in committed fixtures.
- Line 121 also runs a real `kick` without `--dry-run`. It stays safe only because the fake account happens to be unusable.

**Root cause:** The script assumes `PATH` stripping gives isolation, which the project has already documented as false. Its validation also checks the wrong property.

**Minimal fix:**
- Set the same explicit fake or absent Antigravity, Codex and Claude binary overrides the bundled-runtime Swift test uses.
- Use `kick --dry-run`.
- Add an allowlist check: every `label` and `account` value in every fixture must be in `{codex (fixture), gemini (fixture), claude (fixture)}`, and no `@` may appear.
- Add `trap 'rm -rf "$FIXTURE_HOME"' EXIT`.

**Regression test:**
- Unit-test the validator: a synthetic fixture with an unknown label or an email address must fail.
- Integration (macOS CI): plant a fake `agy` in a passwd-home candidate path that prints a sentinel identity. Run the generator and assert the sentinel is absent from every fixture and the fake was never executed (it touches a marker file).

### D2 — P2 — A committed test contains what appears to be a real personal account handle
**Confidence:** high that it is not synthetic; medium that it is the operator's real handle.

**Where:**
- `test_account_presentation.py` lines 132–136. The labels are `"codex (<personal handle>)"` and `"claude (<personal handle>)"`. The test name is `test_screenshot_accounts_render_at_normal_terminal_width`.

**Trigger:** Any sdist build or public export.
- Hatchling's sdist includes tracked root tests.
- `scripts/create-public-export.py` line 15 omits only two docs files.

**Wrong outcome:** This breaks `CONTRIBUTING.md` lines 11–14 ("Do not include real account labels…"). The handle would be published to PyPI and to the public export. Elsewhere in this packet such strings were redacted, which suggests this one slipped through.

**Root cause:** The fixture was copied from a real screenshot. There is no hygiene gate on publishable files.

**Minimal fix:**
- Replace the labels with synthetic ones such as `codex (example)`.
- Add a pre-export and pre-release scan against a denylist that is kept outside the repo, for example in a CI secret.

**Regression test:** `test_repo_hygiene.py` iterates over `git ls-files` and asserts:
- no entry from `$TK_PRIVATE_DENYLIST` appears (skip if the variable is unset), and
- no non-example email domains appear.

Also run the same check inside `create-public-export.py` before copying.

### D3 — P2 — On macOS the PID-file process identity depends on locale and timezone, so the CLI and the app/LaunchAgent can disagree
**Confidence:** medium-high. It relies on BSD `ps` formatting `lstart` with the locale's `%c`.

**Where:** `tokenkick/versioning.py` lines 86–104, specifically the `ps -p PID -o lstart=` call at lines 97–98.

**Trigger:**
- A daemon is started by TokenKick.app or a LaunchAgent. Its environment has no `LANG`, so it uses the C locale.
- It writes `process_identity=Tue Oct 13 14:02:11 2026`.
- The user then runs `tk daemon --stop` or `tk update --yes` in Terminal with `LANG=de_DE.UTF-8`.
- `ps` now prints `Di 13 Okt 14:02:11 2026`.
- A difference in `TZ` between the two environments has the same effect.

**Wrong outcome:**
- `pidfile_process_matches` returns false.
- By design (TOKENKICK_COMMANDS lines 1006–1008) TokenKick then refuses to signal the process.
- The CLI cannot stop or restart a live daemon, status shows it as stale or unverified, and the CLI and app disagree about ownership.

**Root cause:** A process identity is built from human-readable, locale- and timezone-dependent output.

**Minimal fix:**
- Call `subprocess.run(..., env={**os.environ, "LC_ALL": "C", "TZ": "UTC"})`.
- Better: read the start time directly via `sysctl KERN_PROC_PID` (`kp_proc.p_starttime`) through ctypes.

**Regression test:**
- Monkeypatch `subprocess.run` to capture `env`. Assert `LC_ALL == "C"` and `TZ == "UTC"`.
- macOS-only integration test: compute `process_identity(os.getpid())` in child processes with `LANG=de_DE.UTF-8 TZ=Asia/Tokyo` and with `LANG=C TZ=UTC`. Assert the results are equal. Skip if the locale is unavailable.

### D4 — P2 — The agent playbook and command docs teach plan apply without the reviewed-preview binding
**Confidence:** high for the docs. Medium on which failure mode applies, because the CLI's enforcement is not shown.

**Where:**
- `docs/AGENT_PLAYBOOK.md` lines 266–271, 306–311 and 325
- `docs/TOKENKICK_COMMANDS.md` lines 163–164 and 242–244, contradicting the same file at lines 990–997
- `docs/README.md` line 414

**Trigger:**
- An agent previews a plan and the user approves it.
- Cached state or pending kicks then change.
- The agent runs the documented `tk plan --work-window … --apply --yes --json-output`, which has no `--expected-plan` or `--preview-at`.

**Wrong outcome:** One of two things happens:
- **(a)** Apply rebuilds and writes a different plan from the one approved. This bypasses audit item 16. The test inventory's `test_plan_apply_yes_writes_only_orchestrated_pending_kicks` suggests the fingerprint is optional.
- **(b)** Apply rejects the documented command.

**Root cause:** The safeguard section was added without updating the agent-facing examples.

**Minimal fix:**
- Change every agent-facing apply example to `--expected-plan <plan_fingerprint> --preview-at <built_at>`.
- Consider requiring both flags when `--yes` is used with `TK_NO_INTERACTIVE=1`.

**Regression test:**
- Extend `test_docs.py`: every documented `plan … --apply --yes` line must contain both flags.
- If enforcement is adopted, add a CLI test that non-interactive apply without them returns exit 2 and makes no pending writes.

### D5 — P3 — A catch-all `TypeError` fallback re-runs the whole discovery (live provider reads) with fewer guards
**Confidence:** high.

**Where:** `tokenkick/discovery.py` lines 79–85 and 158–164. The degraded branch is at line 424.

**Trigger:** Any `TypeError` raised inside discovery. Examples: a parser comparing `None` to a number, or a malformed CodexBar managed-account entry.

**Wrong outcome:**
- Discovery runs a second time with no `config` and `prepare_claude_setup=False`. That means a second round of Codex app-server launches and Antigravity CLI sessions.
- Claude switches from `_fetch_status(account, config)` to bare `fetch_status(account)`.
  - This plausibly bypasses the config-aware probe context and rate-limit cooldown added in this batch. That part is a hypothesis.
- The original exception is masked if the retry succeeds, or its context is lost if the retry also fails.

**Root cause:** A shim meant for old-signature test doubles leaked into production.

**Minimal fix:** Delete both shims and update test doubles to the real signatures. Alternatively, check `inspect.signature` once without calling.

**Regression test:** Monkeypatch `_discover_direct_accounts(config=None, *, prepare_claude_setup=False)` to count calls and raise `TypeError("boom")`. Assert exactly one call and that the error propagates. Repeat for `_discover_accounts_and_statuses`.

### D6 — P3 — The public docs contradict each other on what can be kicked, on what a Claude session kick does, and on Spark
**Confidence:** high.

**Where, and what each source claims:**

**Antigravity**
- `docs/README.md` lines 498–503 and 572–574 say Antigravity is "monitor-only and never kicked" and that public paths "reject Antigravity".
- `README.md` lines 126–133 and `docs/PROVIDERS.md` lines 170–217 say native Antigravity CLI families are kickable and auto-enable-able.

**Claude session kicks**
- `docs/HOW_TOKENKICK_WORKS.md` lines 485–492 and `docs/PROVIDERS.md` lines 150–152 say a due Claude session uses `/usage` as the kick.
- `docs/TOKENKICK_COMMANDS.md` lines 784–786 and `docs/README.md` lines 524–526 say it sends a completion first, then reads `/usage`.

**Spark**
- `docs/README.md` lines 258–265 and HOW_TOKENKICK_WORKS lines 147–157 still describe Spark sibling discovery as a current feature.
- `scripts/render-readme-demo.py` lines 258–265 and 292–304 render a "codex-spark (lab) … Kick now" row into the README demo, although Spark was retired.

**Model override**
- `docs/README.md` line 445 says `tk model clear` "uses the provider default model". The economical-selection docs contradict this.

**Trigger and outcome:** A user or agent reads the wrong page and misjudges which accounts automation can touch, and what a Claude kick costs.

**Minimal fix:**
- Reconcile `docs/README.md` and HOW_TOKENKICK_WORKS with PROVIDERS and COMMANDS.
- Remove Spark from the demo and re-render it.

**Regression test:** In `test_docs.py`, add negative assertions:
- Antigravity is not described as "never kicked".
- Neither "codex-spark" nor "Spark quota bucket, TokenKick discovers it" appears in user docs or demo text.
- "provider default model" is absent.

### D7 — P3 — The shipped systemd units do not match the documented install
**Confidence:** high on the mismatch; medium on how much it matters.

**Where:**
- `tokenkick@.service` lines 8 and 11
- `tokenkick-telegram@.service` lines 8 and 11
- `docs/README.md` lines 529–534 and 31–33
- `docs/MCP.md` line 192 (the deployed server uses `/home/tokenkick/.local/bin`)

**Trigger:** The user follows the README: `pipx install tokenkick` as a user, then copies the unit.

**Wrong outcome:**
- `ExecStart=/usr/local/bin/tk` does not exist, so the unit fails with `203/EXEC`.
- `HOME=/home/%i` is wrong for system users whose home is under `/var/lib`.

**Minimal fix:** Either document a `systemctl edit` drop-in that sets `ExecStart=` to the path from `command -v tk`, or ship an installer that substitutes the path. Also derive `HOME` instead of hardcoding it.

**Regression test:** A packaging test parses both units. It asserts that either `ExecStart` is a documented placeholder, or that the README service section contains the override step.

### D8 — P3 — The local DMG script leaks a mounted image on failure, and later runs then fail
**Confidence:** high.

**Where:** `scripts/create-local-dmg.sh` lines 27, 29 and 46–66.

**Trigger:** `osascript` fails after `hdiutil attach`. This happens over SSH, in CI, or when Finder Automation permission is denied (error −1743). Because the script uses `set -eu`, it exits at that point.

**Wrong outcome:**
- The read-write image stays mounted at the fixed `MOUNT_DIR`.
- On the next run, `rm -rf "$MOUNT_DIR"` deletes the mounted volume's contents, then fails on the busy mount point.
- Packaging keeps failing until someone detaches the image by hand.

**Minimal fix:**
- Use `MOUNT_DIR="$(mktemp -d)"`.
- Add `trap 'hdiutil detach "$MOUNT_DIR" -force >/dev/null 2>&1 || true' EXIT` immediately after attach.
- Make the Finder layout step optional or non-fatal.

**Regression test:** A pytest test runs the script with stub `hdiutil` and `osascript` on `PATH`. The `osascript` stub exits 1. Assert the `hdiutil` stub log contains `detach`, and that a second run reaches `create`.

---

## B. Hypotheses (these need code or platform evidence outside this packet)

**H1 — P2 — The unpublished batch reports version 1.22.0, so `tk update` cannot see that the running daemon is on old code.**
- **Evidence:** `pyproject.toml` line 7; `versioning.installed_version` lines 26–33; batch docs ("no version bump").
- **Trigger:**
  1. Install the batch on the server via a pipx reinstall at the same version.
  2. `tk update --check` reports up to date.
  3. `tk stagger apply` turns on master and session for both Antigravity families.
  4. The old 1.22.0 daemon, which has Antigravity automation but no stagger ownership, kicks both families independently.
- **Fix:**
  - Bump to a distinct version (for example `1.23.0.dev0`) before any deployment.
  - Add a capability or schema revision to the PID file.
  - Have `stagger apply` refuse when the running daemon does not advertise stagger support.
- **Test:** Start from a PID file with the same version and no capability. Assert `stagger apply` returns `daemon_capability_missing`, and that `tk update --check` exits 1.

**H2 — P2 — systemd and TokenKick can both supervise the same process.**
- **Evidence:**
  - The daemon supervises the Telegram listener (`test_daemon_supervises_enabled_telegram_remote`, `_supervise_telegram_remote`).
  - The docs also recommend the `tokenkick-telegram@` unit (TOKENKICK_COMMANDS lines 402–404).
  - `tk update` restarts background processes, while the safeguards text says to stop via the service manager.
- **Risks:**
  - During systemd's 30-second `RestartSec` gap, the daemon starts its own detached listener. The unit then flaps on a duplicate-refusal exit.
  - `tk update` kills a unit-managed daemon. With `Restart=on-failure`, a SIGTERM exit is treated as clean, so systemd does not restart it. The replacement then runs outside journald and the unit.
- **Test:** Simulate a PID file owned by systemd (or check `INVOCATION_ID`). Assert that update and supervision decline and tell the user to run `systemctl restart`.

**H3 — P2/P3 — Global reset defense can be triggered by sliding clocks on idle, unanchored windows after a polling gap.**
- **Evidence:**
  - `reset_defense.py` lines 638–643: `reset_shift` needs only a shift of more than one hour, with no usage condition.
  - Lines 791–806: three rows, or a previous-reset spread over 48 hours, makes the event `confirmed`.
  - Lines 292–302: a confirmed event invalidates pending kicks.
  - Lines 280–283: independence is computed only for Codex homes.
- **Trigger:** A Mac sleeps for 8 hours with an idle sliding Antigravity or Codex window (`resets_at ≈ now + window`). On wake, another same-provider account has naturally reset. The previous-reset spread exceeds 48 hours, so the event is confirmed, a false "global reset" notification goes out and pending kicks are invalidated.
- **Status:** This depends on whether sliding windows populate `resets_at`. The docs' "sliding reset timestamp" wording suggests they do.
- **Test:** Two accounts on one provider. One is idle with previous `resets_at = prev_obs + 7d` and current `= now + 7d`. The other naturally resets. The gap is 8 hours. Expect no global event.

**H4 — P3 — The public export reads `git ls-files` but copies working-tree content.**
- **Evidence:** `create-public-export.py` lines 13–28.
- **Trigger:** New modules in this uncommitted batch (`stagger.py`, `claude_monitoring.py`, `codex_limits.py`, `antigravity_windows.py`, new tests) are still untracked while their callers are modified.
- **Outcome:** The export contains imports of files it does not include. A missing tracked file also leaves a partial destination, which the script will then refuse to overwrite.
- **Fix:** Export from a commit with `git archive`, or refuse when `git status --porcelain` is non-empty.

**H5 — P3 — The Developer ID path is unexercised, and the entitlement is on the wrong binary.**
- **Evidence:** `packaging/macos/TokenKick.entitlements` lines 5–9; `sign-macos-app.sh` lines 57–64.
- **The mismatch:**
  - `disable-library-validation` is applied only to the Swift main executable, which never loads PyInstaller libraries.
  - It weakens the app's protection against dylib injection for no benefit.
  - The `tk` executable in `Contents/Resources` gets no entitlements under the hardened runtime.
- **Why it is untested:** Public betas are ad-hoc and not notarized (PROVIDER_COMPATIBILITY_VERIFICATION lines 38–39), so notarization, the hardened runtime and stapling have never run end to end.
- **Outcome:** Under `HARDENED_RUNTIME=1` with ad-hoc signing, team-less library validation would likely break `tk`.

**H6 — P3 — Packaging may ship a Swift binary built from stale incremental artifacts.**
- **Evidence:** `build-swift-app.sh` line 11 reuses `build/macos-swift`. The stagger doc (lines 222–226) records the test runner crashing on incremental builds after the model-layout change, with a clean build passing.
- **Fix:** Use a clean scratch path for release packaging.

---

## C. Design improvements (5)

1. **Make the test suite hermetic.**
   - `test_notification_settings.py` lines 16–22 patches only `models.CONFIG_DIR` and `CONFIG_FILE`. Constants copied at import time (such as `RESET_EVENTS_FILE`, status-cache and pending files) still point at the developer's real `~/.tokenkick`.
   - `test_cli`'s `isolate_status_cache` fixture is local to that file.
   - Add a root `conftest.py` with an autouse fixture that:
     - sets `HOME` and a TokenKick state-dir override to `tmp_path`, and
     - fails on unexpected `subprocess`, socket or `httpx` use unless the test opts in.
   - Passing counts such as 1,831 / 203 currently cannot rule out host leakage.

2. **Make release builds reproducible and publish the artifact that was verified.**
   - CI installs unpinned dependencies with `pip install -e '.[dev]'` (the Ruff 0.17 incident shows the risk).
   - PyInstaller and `hatchling` are unpinned.
   - Actions are pinned by tag, and `gh-action-pypi-publish@release/v1` uses a mutable branch.
   - `release.yml` rebuilds on Python 3.12 instead of reusing the wheel that `verify` checked.
   - Fixes:
     - Use the lockfile in CI.
     - Pin GitHub Actions to commit SHAs.
     - Add a tag-equals-`pyproject` version check.
     - Run `twine check` and `verify-package-resources.py` on the exact artifact being published.
     - Remove `|| true` from DMG verification (`verify-macos-package.sh` line 49).

3. **Make safety defaults hold by construction.**
   - Discovery currently builds Codex, Claude and CodexBar accounts with `auto_kick=True, session_auto_kick=True` (`discovery.py` lines 295–296, 415–416 and 613–614). Every save path has to remember to override this through `_with_setup_auto_kick_defaults`. Default to `False` at construction instead.
   - `recovery_hints.py` lines 26–42 tells the doctor (and through it MCP agents) to run `tk kick … --force` because of a stale refresh. That is exactly the evidence class the hardening work rejects, and it contradicts AGENT_PLAYBOOK ("Never run tk kick --force"). Recommend a re-auth and refresh instead.

4. **Clean up public-doc hygiene.**
   - Move batch verification notes (Hetzner, backup file counts, "five remaining server accounts", private-record references) out of the public `docs/`.
   - Switch the export from a two-file denylist to an allowlist.
   - Generate the command reference from the Click tree and add contradiction checks to `test_docs.py`.

5. **Add a runtime capability handshake.**
   - Write a feature or schema revision into the daemon and Telegram PID files and into `app snapshot`.
   - Use distinct dev versions for unpublished batches.
   - Have the CLI, MCP and Swift refuse policy changes the running daemon cannot honour (stagger now, others later).
   - Also add systemd hardening to the units (`NoNewPrivileges`, `ProtectSystem`, `PrivateTmp`, and `Wants=`/`After=network-online.target`).

---

## D. Coverage matrix

| Area | Depth | Notes |
|---|---|---|
| CI `verify.yml` / `release.yml` | Full | No tag-version check; release rebuilds instead of reusing the verified artifact |
| `pyproject.toml` | Full | Unpinned build backend |
| Bundled runtime, assemble, sign, notarize, DMG, verify scripts | Full | D8, H5, H6 |
| Export, fixture generator, demo renderer | Full | D1, D6, H4 |
| systemd units | Full | D7, H2 |
| Public and batch docs | Full for the supplied set | `CHANGELOG`, `RELEASE.md`, `APP_UX_PLAN.md` and `llms.txt` not supplied |
| `test_selection_menus`, `test_notification_settings`, `test_account_presentation`, `test_cli` diff | Full | D2; isolation gap |
| Other `test_cli`, `test_docs`, `test_hardening`, `test_reset_defense` | Inventory only | Assertions not verifiable |
| `versioning`, `discovery`, `reset_defense`, `recovery_hints`, `source_utils` | Full | D3, D5, H3 |
| Swift sources and Swift tests | Not in this slice | Covered by other calls |
| `conftest.py`, lockfile, `cli.py` update/supervision, `mcp_core._read_repo_text` | Not supplied | H1 and H2 depend on these |

## E. Residual uncertainty

- **Packet artifacts:** The `REDACTED_EMAIL` entries in `generate-macos-icon.sh` lines 56–64 are redaction artifacts. The `@2x` icon names could not be checked.
- **Platform and environment behaviour:** The platform-dependent claims were not reproduced here. These are BSD `ps` locale handling (D3), PyInstaller metadata collection, and the notarization and stapling semantics (H5). The fixture-generator leak (D1) depends on the host environment.
- **Test counts:** I have no independent confirmation of the reported 1,831 Python and 203 Swift test results.