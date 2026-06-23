"""One-shot and compatibility migrations for TokenKick persisted state."""

from __future__ import annotations

import json
import re
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

import click

from .direct import CODEX_PROVIDER_USAGE_SOURCE_DETAIL, read_codex_identity
from .models import (
    CONFIG_DIR,
    CONFIG_FILE,
    CODEX_DEFAULT_RATE_LIMIT_ID,
    AccountConfig,
    AccountState,
    AccountStatus,
    Config,
    DataSource,
    ScheduleConfig,
    account_key_string,
)
from .scheduling import (
    PendingKick,
    PendingKickStateError,
    from_utc_iso,
    load_pending_kicks,
    save_pending_kicks,
)

DIRECT_SOURCE_MIGRATION_KEY = "v0.4-direct-sources-v2"
DIRECT_SOURCE_BACKUP_FILE = CONFIG_FILE.with_name("config.json.pre-v0.4-backup")
DIRECT_SOURCE_APPSERVER_BACKUP_FILE = CONFIG_FILE.with_name(
    "config.json.pre-v0.4x-appserver-backup"
)
LABEL_FORMAT_MIGRATION_KEY = "provider-first-labels"
CODEX_HOME_KEY_MIGRATION_KEY = "v0.5-codex-home-keys-v1"
CODEX_HOME_IDENTITY_REPAIR_BACKUP_FILE = CONFIG_FILE.with_name(
    "config.json.pre-codex-home-identity-repair-backup"
)
LABEL_FORMAT_BACKUP_FILE = CONFIG_FILE.with_name("config.json.pre-label-format-backup")


def _cli():
    from . import cli as cli_mod

    return cli_mod


def _migrate_v04_direct_sources_if_needed(
    config: Config,
    *,
    emit_notice: bool = True,
    recheck_skipped: bool = False,
) -> Config:
    """Migrate confidently matched v0.3 CodexBar accounts to v0.4 direct sources."""
    migration_complete = config.migrations.get(DIRECT_SOURCE_MIGRATION_KEY)
    if migration_complete and not recheck_skipped:
        return config
    if not config.loaded_from_file:
        return config
    if not CONFIG_FILE.exists():
        return config
    if not config.accounts:
        return config

    direct_accounts, direct_statuses = _cli()._discover_direct_accounts()
    direct_accounts, direct_statuses = _cli()._merge_discovered_accounts(
        list(zip(direct_accounts, direct_statuses, strict=False))
    )
    direct_statuses = [
        _codex_direct_status_for_migration(account, status)
        for account, status in zip(direct_accounts, direct_statuses, strict=False)
    ]
    direct_status_by_key = {
        account_key_string(account): status
        for account, status in zip(direct_accounts, direct_statuses, strict=False)
    }
    migrated_accounts: list[AccountConfig] = []
    old_keys: list[str] = []
    migrated_labels: list[str] = []
    provider_usage_upgraded_labels: list[str] = []
    repaired_labels: list[str] = []
    legacy_claude_count = sum(
        1
        for account in config.accounts
        if account.provider == "claude" and account.source == DataSource.CODEXBAR_CLI
    )

    for account in config.accounts:
        if (
            migration_complete
            and recheck_skipped
            and account.provider == "codex"
            and account.source == DataSource.CODEX_DIRECT
            and not _codex_direct_readable(
                account,
                direct_status_by_key.get(account_key_string(account)),
            )
            and account.codexbar_account
        ):
            old_keys.append(account_key_string(account))
            repaired_labels.append(f"{account.label} ({account.provider})")
            migrated_accounts.append(_codexbar_account_from_direct(account))
            continue

        direct_account = _direct_migration_match(
            account,
            direct_accounts,
            direct_status_by_key=direct_status_by_key,
            legacy_claude_count=legacy_claude_count,
        )
        if direct_account is None:
            migrated_accounts.append(account)
            continue
        old_keys.append(account_key_string(account))
        direct_status = direct_status_by_key.get(account_key_string(direct_account))
        if (
            migration_complete
            and recheck_skipped
            and account.provider == "codex"
            and direct_status is not None
            and direct_status.source_detail == CODEX_PROVIDER_USAGE_SOURCE_DETAIL
        ):
            provider_usage_upgraded_labels.append(f"{account.label} ({account.provider})")
        else:
            migrated_labels.append(f"{account.label} ({account.provider})")
        migrated_accounts.append(
            replace(
                direct_account,
                label=account.label,
                label_origin=account.label_origin,
                auto_kick=account.auto_kick,
                weekly_auto_kick=account.weekly_auto_kick,
                session_auto_kick=account.session_auto_kick,
                visible=account.visible,
                status_probe_enabled=account.status_probe_enabled,
                direct_usage_enabled=account.direct_usage_enabled,
                kick_model=account.kick_model,
            )
        )

    updated_config = replace(
        config,
        accounts=migrated_accounts,
        migrations={**config.migrations, DIRECT_SOURCE_MIGRATION_KEY: True},
    )
    changed = (
        not migration_complete
        or bool(migrated_labels)
        or bool(provider_usage_upgraded_labels)
        or bool(repaired_labels)
    )
    if changed:
        if provider_usage_upgraded_labels and migration_complete:
            _write_config_backup(DIRECT_SOURCE_APPSERVER_BACKUP_FILE)
        else:
            _write_pre_v04_config_backup()
        _prune_migrated_status_cache_entries(old_keys)
        updated_config.save()

    if emit_notice and changed:
        if provider_usage_upgraded_labels:
            upgraded = ", ".join(provider_usage_upgraded_labels)
            noun = "account" if len(provider_usage_upgraded_labels) == 1 else "accounts"
            click.echo(
                "Upgrading direct Codex provider usage reads for "
                f"{upgraded}. Previous CodexBar fallback is no longer needed for "
                f"this {noun}. Backup: {DIRECT_SOURCE_APPSERVER_BACKUP_FILE}",
                err=True,
            )
        if migrated_labels:
            migrated = ", ".join(migrated_labels)
            click.echo(
                "TokenKick migrated direct provider sources for "
                f"{migrated}. Backup: {DIRECT_SOURCE_BACKUP_FILE}",
                err=True,
            )
        if repaired_labels:
            repaired = ", ".join(repaired_labels)
            click.echo(
                "TokenKick kept unreadable Codex direct accounts on CodexBar for "
                f"{repaired}. Backup: {DIRECT_SOURCE_BACKUP_FILE}",
                err=True,
            )
        if not migrated_labels and not provider_usage_upgraded_labels and not repaired_labels:
            click.echo(
                "TokenKick v0.4 direct-provider migration found no confident account "
                f"matches. Existing accounts were left unchanged. Backup: {DIRECT_SOURCE_BACKUP_FILE}",
                err=True,
            )
    return updated_config


def _direct_migration_match(
    account: AccountConfig,
    direct_accounts: list[AccountConfig],
    *,
    direct_status_by_key: dict[str, AccountStatus],
    legacy_claude_count: int,
) -> AccountConfig | None:
    if account.source != DataSource.CODEXBAR_CLI:
        return None
    if account.provider == "codex" and account.codexbar_account:
        legacy_email = account.codexbar_account.lower()
        matches = [
            direct
            for direct in direct_accounts
            if direct.provider == "codex"
            and direct.identity_email
            and direct.identity_email.lower() == legacy_email
            and _codex_direct_readable(direct, direct_status_by_key.get(account_key_string(direct)))
        ]
        return matches[0] if len(matches) == 1 else None
    if account.provider == "claude":
        direct_claude = [direct for direct in direct_accounts if direct.provider == "claude"]
        if account.codexbar_account:
            legacy_email = account.codexbar_account.lower()
            matches = [
                direct
                for direct in direct_claude
                if direct.identity_email and direct.identity_email.lower() == legacy_email
                and _claude_direct_cli_usage_readable(
                    direct, direct_status_by_key.get(account_key_string(direct))
                )
            ]
            return matches[0] if len(matches) == 1 else None
        readable = [
            direct
            for direct in direct_claude
            if _claude_direct_cli_usage_readable(
                direct, direct_status_by_key.get(account_key_string(direct))
            )
        ]
        if legacy_claude_count == 1 and len(readable) == 1:
            return readable[0]
    return None


def _claude_direct_cli_usage_readable(
    account: AccountConfig,
    status: AccountStatus | None,
) -> bool:
    return (
        account.provider == "claude"
        and account.source == DataSource.CLAUDE_DIRECT
        and status is not None
        and status.state != AccountState.UNKNOWN
        and status.source_detail == "claude-cli-usage"
    )


def _codex_direct_readable(
    account: AccountConfig,
    status: AccountStatus | None,
) -> bool:
    if account.provider != "codex" or account.source != DataSource.CODEX_DIRECT:
        return False
    if _codex_status_from_direct_source(status):
        return True
    return _codex_sessions_dir_exists(account)


def _codex_status_from_direct_source(status: AccountStatus | None) -> bool:
    return (
        status is not None
        and status.state != AccountState.UNKNOWN
        and status.source_detail in {"codex-session-jsonl", CODEX_PROVIDER_USAGE_SOURCE_DETAIL}
    )


def _codex_sessions_dir_exists(account: AccountConfig) -> bool:
    sessions_dir = Path(
        account.session_path
        or Path(account.provider_home or Path.home() / ".codex") / "sessions"
    )
    return sessions_dir.exists()


def _codex_direct_status_for_migration(
    account: AccountConfig,
    status: AccountStatus,
) -> AccountStatus:
    if (
        account.provider != "codex"
        or account.source != DataSource.CODEX_DIRECT
        or _codex_direct_readable(account, status)
    ):
        return status
    retry = _cli()._fetch_status(account)
    if _codex_status_from_direct_source(retry):
        return retry
    return status


def _unreadable_codex_direct_duplicate_exists(
    account: AccountConfig,
    status: AccountStatus,
    existing_keys: set[tuple[str, str, str]],
) -> bool:
    return (
        account.source == DataSource.CODEX_DIRECT
        and account.provider == "codex"
        and account.identity_email is not None
        and not _codex_direct_readable(account, status)
        and ("account", "codex", account.identity_email.lower()) in existing_keys
    )


def _volatile_primary_codex_duplicate_exists(
    account: AccountConfig,
    existing_accounts: list[AccountConfig],
) -> bool:
    if (
        account.provider != "codex"
        or account.source != DataSource.CODEX_DIRECT
        or Path(account.provider_home or "") != Path.home() / ".codex"
    ):
        return False
    return any(
        existing.provider == "codex"
        and existing.source == DataSource.CODEX_DIRECT
        and Path(existing.provider_home or "") != Path.home() / ".codex"
        and _codex_identity_metadata_matches(existing, account)
        for existing in existing_accounts
    )


def _codexbar_account_from_direct(account: AccountConfig) -> AccountConfig:
    return replace(
        account,
        source=DataSource.CODEXBAR_CLI,
        codexbar_provider="codex",
        session_path=None,
        provider_home=None,
        identity_provider_id=None,
        identity_email=None,
        identity_org_id=None,
    )


def _write_pre_v04_config_backup() -> None:
    _write_config_backup(DIRECT_SOURCE_BACKUP_FILE)


def _write_config_backup(path: Path) -> None:
    if not CONFIG_FILE.exists() or path.exists():
        return
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    path.write_text(CONFIG_FILE.read_text())


def _prune_migrated_status_cache_entries(old_keys: list[str]) -> None:
    if not old_keys:
        return
    data = _cli()._read_status_cache_data()
    if data is None:
        return
    accounts = data.get("accounts")
    if not isinstance(accounts, dict):
        return
    changed = False
    for key in old_keys:
        if key in accounts:
            del accounts[key]
            changed = True
    if changed:
        _cli()._write_status_cache_data(data)


def _migrate_codex_home_keys_if_needed(
    config: Config,
    *,
    emit_notice: bool = True,
) -> Config:
    if config.migrations.get(CODEX_HOME_KEY_MIGRATION_KEY):
        return config
    if not config.loaded_from_file:
        return config
    if not CONFIG_FILE.exists():
        return config

    key_map: dict[str, str] = {}
    account_by_new_key: dict[str, AccountConfig] = {}
    for account in config.accounts:
        old_key = _legacy_codex_direct_identity_key(account)
        if old_key is None:
            continue
        new_key = account_key_string(account)
        if old_key == new_key:
            continue
        key_map[old_key] = new_key
        account_by_new_key[new_key] = account

    if not key_map:
        return config

    cache_changed = _migrate_status_cache_keys(key_map, account_by_new_key)
    pending_changed = _migrate_pending_keys(key_map, account_by_new_key)
    updated = replace(
        config,
        migrations={**config.migrations, CODEX_HOME_KEY_MIGRATION_KEY: True},
    )
    updated.save()
    if emit_notice and (cache_changed or pending_changed):
        click.echo(
            "TokenKick migrated Codex direct account state to home-scoped keys.",
            err=True,
        )
    return updated


def _repair_codex_home_identity_drift_if_needed(
    config: Config,
    *,
    emit_notice: bool = True,
) -> Config:
    if not config.loaded_from_file or not CONFIG_FILE.exists() or not config.accounts:
        return config

    managed_by_identity = _managed_codex_accounts_by_identity()
    if not managed_by_identity:
        return config

    changed = False
    old_keys_to_prune: list[str] = []
    pending_key_map: dict[str, str] = {}
    pending_accounts_by_new_key: dict[str, AccountConfig] = {}
    repaired_labels: list[str] = []
    repaired_accounts: list[AccountConfig] = []

    for account in config.accounts:
        repaired = _repair_codex_home_identity_drift_account(account, managed_by_identity)
        if repaired is None:
            repaired_accounts.append(account)
            continue

        old_key = account_key_string(account)
        new_key = account_key_string(repaired)
        old_keys_to_prune.append(old_key)
        if old_key != new_key:
            pending_key_map[old_key] = new_key
            pending_accounts_by_new_key[new_key] = repaired
        repaired_accounts.append(repaired)
        repaired_labels.append(account.label)
        changed = True

    if not changed:
        return config

    _write_config_backup(CODEX_HOME_IDENTITY_REPAIR_BACKUP_FILE)
    _prune_migrated_status_cache_entries(old_keys_to_prune)
    _migrate_pending_keys(pending_key_map, pending_accounts_by_new_key)
    updated = replace(config, accounts=repaired_accounts)
    updated.save()
    if emit_notice:
        labels = ", ".join(repaired_labels)
        click.echo(
            "TokenKick repaired Codex account homes after auth identity drift: "
            f"{labels}. Backup: {CODEX_HOME_IDENTITY_REPAIR_BACKUP_FILE}",
            err=True,
        )
    return updated


def _repair_codex_home_identity_drift_account(
    account: AccountConfig,
    managed_by_identity: dict[tuple[str, str], dict[str, str]],
) -> AccountConfig | None:
    if (
        account.provider != "codex"
        or account.source != DataSource.CODEX_DIRECT
        or not _cli()._codex_has_home_scope(account)
    ):
        return None
    if _codex_configured_home_identity_current(account):
        return None

    managed = _managed_codex_account_for_configured_identity(account, managed_by_identity)
    if managed is None:
        return None
    managed_home = managed.get("managed_home")
    if not managed_home or managed_home == account.provider_home:
        return None

    managed_email = managed.get("email") or account.identity_email
    managed_provider_id = managed.get("provider_id") or account.identity_provider_id
    return replace(
        account,
        provider_home=managed_home,
        session_path=str(Path(managed_home) / "sessions"),
        codexbar_account=managed_email,
        identity_provider_id=managed_provider_id,
        identity_email=managed_email,
    )


def _managed_codex_account_for_configured_identity(
    account: AccountConfig,
    managed_by_identity: dict[tuple[str, str], dict[str, str]],
) -> dict[str, str] | None:
    if account.identity_provider_id:
        match = managed_by_identity.get(("id", account.identity_provider_id))
        if match is not None:
            return match
    if account.identity_email:
        return managed_by_identity.get(("email", account.identity_email.lower()))
    return None


def _managed_codex_accounts_by_identity() -> dict[tuple[str, str], dict[str, str]]:
    path = (
        Path.home()
        / "Library"
        / "Application Support"
        / "CodexBar"
        / "managed-codex-accounts.json"
    )
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return {}

    by_identity: dict[tuple[str, str], dict[str, str]] = {}
    accounts = data.get("accounts") if isinstance(data, dict) else None
    if not isinstance(accounts, list):
        return {}
    for entry in accounts:
        if not isinstance(entry, dict):
            continue
        managed_home = entry.get("managedHomePath")
        if not isinstance(managed_home, str) or not managed_home:
            continue
        managed: dict[str, str] = {"managed_home": managed_home}
        email = entry.get("email")
        provider_id = entry.get("providerAccountID")
        if isinstance(email, str) and email:
            managed["email"] = email
            by_identity[("email", email.lower())] = managed
        if isinstance(provider_id, str) and provider_id:
            managed["provider_id"] = provider_id
            by_identity[("id", provider_id)] = managed
    return by_identity


def _codex_configured_home_identity_current(account: AccountConfig) -> bool:
    actual = _read_codex_account_home_identity(account)
    if actual is None:
        return False
    return _codex_identity_metadata_matches(account, actual)


def _codex_configured_home_identity_not_mismatched(account: AccountConfig) -> bool:
    actual = _read_codex_account_home_identity(account)
    if actual is None:
        return True
    return _codex_identity_metadata_matches(account, actual)


def _read_codex_account_home_identity(account: AccountConfig) -> AccountConfig | None:
    codex_home = _codex_home_for_account(account)
    if codex_home is None:
        return None
    identity = read_codex_identity(codex_home)
    if identity is None:
        return None
    return replace(
        account,
        identity_provider_id=identity.provider_account_id,
        identity_email=identity.email,
    )


def _codex_home_for_account(account: AccountConfig) -> Path | None:
    if account.provider != "codex" or account.source != DataSource.CODEX_DIRECT:
        return None
    if account.provider_home:
        return Path(account.provider_home)
    if account.session_path:
        session_path = Path(account.session_path)
        return session_path.parent if session_path.name == "sessions" else session_path
    return None


def _codex_identity_metadata_matches(
    expected: AccountConfig,
    actual: AccountConfig,
) -> bool:
    if (
        expected.identity_provider_id
        and actual.identity_provider_id
        and expected.identity_provider_id != actual.identity_provider_id
    ):
        return False
    if (
        expected.identity_email
        and actual.identity_email
        and expected.identity_email.lower() != actual.identity_email.lower()
    ):
        return False
    return True


def _legacy_codex_direct_identity_key(account: AccountConfig) -> str | None:
    if account.provider != "codex" or account.source != DataSource.CODEX_DIRECT:
        return None
    if not account.provider_home and not account.session_path:
        return None
    if account.identity_provider_id:
        return "|".join(("identity", "codex", account.identity_provider_id))
    if account.identity_email:
        return "|".join(("identity", "codex", account.identity_email.lower()))
    if account.codexbar_account:
        return "|".join(("account", "codex", account.codexbar_account.lower()))
    return None


def _migrate_status_cache_keys(
    key_map: dict[str, str],
    account_by_new_key: dict[str, AccountConfig],
) -> bool:
    if not key_map:
        return False
    entries = _cli()._load_status_cache_entries()
    if not entries:
        return False
    migrated: dict[str, dict] = {}
    changed = False
    for key, entry in entries.items():
        new_key = key_map.get(key, key)
        account = account_by_new_key.get(new_key, entry["account"])
        updated_entry = {**entry, "account": account}
        if new_key in migrated and new_key != key:
            changed = True
            continue
        migrated[new_key] = updated_entry
        changed = changed or new_key != key

    if not changed:
        return False
    _cli()._write_status_cache_data(_cli()._status_cache_data(migrated))
    return True


def _migrate_pending_keys(
    key_map: dict[str, str],
    account_by_new_key: dict[str, AccountConfig],
) -> bool:
    if not key_map:
        return False
    pending = load_pending_kicks(datetime.now(timezone.utc))
    if not pending:
        return False
    migrated: dict[str, PendingKick] = {}
    changed = False
    for key, pending_kick in pending.items():
        new_key = key_map.get(key)
        if new_key is None:
            _put_pending_keep_earliest(migrated, key, pending_kick)
            continue
        account = account_by_new_key[new_key]
        pending_kick.account_key = new_key
        pending_kick.account_label = account.label
        pending_kick.provider = account.provider
        _put_pending_keep_earliest(migrated, new_key, pending_kick)
        changed = True
    if changed:
        changed = _save_migrated_pending_kicks(migrated)
    return changed


def _save_migrated_pending_kicks(pending: dict[str, PendingKick]) -> bool:
    """Best-effort persistence of re-keyed pending kicks during migrations."""
    try:
        save_pending_kicks(pending)
    except PendingKickStateError as exc:
        click.echo(f"Warning: migrated pending kicks were not saved: {exc}", err=True)
        return False
    return True


def _migrate_provider_first_labels_if_needed(
    config: Config,
    *,
    emit_notice: bool = True,
) -> Config:
    if config.migrations.get(LABEL_FORMAT_MIGRATION_KEY):
        return config
    if not config.loaded_from_file:
        return config
    if not CONFIG_FILE.exists():
        return config
    if not config.accounts:
        return replace(
            config,
            migrations={**config.migrations, LABEL_FORMAT_MIGRATION_KEY: True},
        )

    migrated_accounts, renamed, skipped = _provider_first_label_migration_accounts(
        config.accounts,
        force_labels=set(),
    )
    updated_schedule = _migrate_schedule_label_keys(config.schedule, renamed)
    updated_config = replace(
        config,
        accounts=migrated_accounts,
        schedule=updated_schedule,
        migrations={**config.migrations, LABEL_FORMAT_MIGRATION_KEY: True},
    )

    _write_label_format_config_backup()
    _migrate_label_keyed_status_cache(config.accounts, migrated_accounts, renamed)
    _migrate_label_keyed_pending_kicks(config.accounts, migrated_accounts, renamed)
    updated_config.save()

    if emit_notice:
        _emit_label_format_migration_notice(renamed, skipped)
    return updated_config


def _provider_first_label_migration_accounts(
    accounts: list[AccountConfig],
    *,
    force_labels: set[str],
) -> tuple[list[AccountConfig], dict[str, str], list[str]]:
    renamed: dict[str, str] = {}
    skipped: list[str] = []
    used_labels = {account.label for account in accounts}
    migrated_accounts: list[AccountConfig] = []

    for account in accounts:
        target = _provider_first_label(account)
        if target is None or target == account.label:
            migrated_accounts.append(account)
            continue

        if account.label in force_labels:
            new_label = _unique_provider_first_migration_label(target, used_labels, account.label)
            renamed[account.label] = new_label
            migrated_accounts.append(replace(account, label=new_label, label_origin="user"))
            continue

        if _is_bare_provider_label(account.label, account):
            skipped.append(account.label)
            migrated_accounts.append(account)
            continue

        if _is_auto_generated_label_pattern(account, target):
            new_label = _unique_provider_first_migration_label(target, used_labels, account.label)
            renamed[account.label] = new_label
            migrated_accounts.append(replace(account, label=new_label, label_origin="auto"))
            continue

        skipped.append(account.label)
        migrated_accounts.append(account)

    return migrated_accounts, renamed, skipped


def _provider_first_label(account: AccountConfig) -> str | None:
    component = _provider_first_account_component(account)
    if not component:
        return None
    rate_limit_id = getattr(account, "codex_rate_limit_id", None)
    provider = (
        "codex-spark"
        if account.provider == "codex" and rate_limit_id and rate_limit_id != CODEX_DEFAULT_RATE_LIMIT_ID
        else account.provider
    )
    return f"{provider} ({component})"


def _provider_first_account_component(account: AccountConfig) -> str | None:
    if account.identity_email:
        return _cli()._label_from_email(account.identity_email)
    if account.codexbar_account:
        return _cli()._label_from_email(account.codexbar_account)
    if account.identity_provider_id:
        return _cli()._sanitize_label(account.identity_provider_id)
    provider = re.escape(account.provider)
    match = re.fullmatch(rf"(.+) \({provider}\)", account.label)
    if match:
        return _cli()._sanitize_label(match.group(1))
    return None


def _is_bare_provider_label(label: str, account: AccountConfig) -> bool:
    providers = {account.provider}
    if account.codexbar_provider:
        providers.add(account.codexbar_provider)
    return label.lower() in {provider.lower() for provider in providers}


def _is_auto_generated_label_pattern(account: AccountConfig, target: str) -> bool:
    component = _provider_first_account_component(account)
    if not component:
        return False
    if account.label == component:
        return True
    return account.label == f"{component} ({account.provider})" and target != account.label


def _unique_provider_first_migration_label(
    target: str,
    used_labels: set[str],
    old_label: str,
) -> str:
    used_without_old = set(used_labels)
    used_without_old.discard(old_label)
    if target not in used_without_old:
        used_labels.discard(old_label)
        used_labels.add(target)
        return target

    index = 2
    while f"{target}-{index}" in used_without_old:
        index += 1
    unique = f"{target}-{index}"
    used_labels.discard(old_label)
    used_labels.add(unique)
    return unique


def _migrate_schedule_label_keys(
    schedule: ScheduleConfig,
    renamed: dict[str, str],
) -> ScheduleConfig:
    if not renamed or not schedule.accounts:
        return schedule
    accounts = dict(schedule.accounts)
    changed = False
    for old_label, new_label in renamed.items():
        old_schedule = accounts.pop(old_label, None)
        if old_schedule is None:
            continue
        if new_label not in accounts:
            accounts[new_label] = old_schedule
        changed = True
    if not changed:
        return schedule
    return replace(schedule, accounts=accounts)


def _write_label_format_config_backup() -> None:
    if not CONFIG_FILE.exists() or LABEL_FORMAT_BACKUP_FILE.exists():
        return
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    LABEL_FORMAT_BACKUP_FILE.write_text(CONFIG_FILE.read_text())


def _emit_label_format_migration_notice(renamed: dict[str, str], skipped: list[str]) -> None:
    if renamed:
        pairs = ", ".join(f"{old} -> {new}" for old, new in renamed.items())
        click.echo(
            "TokenKick migrated account labels to provider-first format: "
            f"{pairs}. Backup: {LABEL_FORMAT_BACKUP_FILE}",
            err=True,
        )
    if skipped:
        skipped_labels = sorted(set(skipped))
        labels = ", ".join(skipped_labels)
        example = f'tk setup --rename-label "{skipped_labels[0]}"'
        click.echo(
            "TokenKick did not rename labels that may be user-defined: "
            f"{labels}. To opt in, run {example}.",
            err=True,
        )


def _migrate_label_keyed_status_cache(
    old_accounts: list[AccountConfig],
    new_accounts: list[AccountConfig],
    renamed: dict[str, str],
) -> None:
    if not renamed:
        return
    entries = _cli()._load_status_cache_entries()
    if not entries:
        return
    account_map = _label_migration_account_map(old_accounts, new_accounts)
    changed = False
    migrated_entries: dict[str, dict] = {}

    for key, entry in entries.items():
        old_account = entry["account"]
        mapping = account_map.get((old_account.label, old_account.provider))
        if mapping is None:
            migrated_entries[key] = entry
            continue
        _old_account, new_account = mapping
        new_key = account_key_string(new_account)
        updated_entry = {
            **entry,
            "account": new_account,
            "status": replace(entry["status"], label=new_account.label),
        }
        if new_key in migrated_entries and new_key != key:
            changed = True
            continue
        migrated_entries[new_key] = updated_entry
        changed = changed or new_key != key or old_account.label != new_account.label

    if not changed:
        return
    _cli()._write_status_cache_data(_cli()._status_cache_data(migrated_entries))


def _migrate_label_keyed_pending_kicks(
    old_accounts: list[AccountConfig],
    new_accounts: list[AccountConfig],
    renamed: dict[str, str],
) -> None:
    if not renamed:
        return
    account_map = _label_migration_account_map(old_accounts, new_accounts)
    account_map_by_old_key = {
        account_key_string(old_account): (old_account, new_account)
        for old_account, new_account in account_map.values()
    }
    pending = load_pending_kicks(datetime.now(timezone.utc))
    if not pending:
        return
    migrated: dict[str, PendingKick] = {}
    changed = False

    for key, pending_kick in pending.items():
        mapping = account_map.get((pending_kick.account_label, pending_kick.provider))
        if mapping is None:
            mapping = account_map_by_old_key.get(key)
        if mapping is None:
            migrated[key] = pending_kick
            continue

        _old_account, new_account = mapping
        new_key = account_key_string(new_account)
        pending_kick.account_key = new_key
        pending_kick.account_label = new_account.label
        pending_kick.provider = new_account.provider
        changed = True
        _put_pending_keep_earliest(migrated, new_key, pending_kick)

    if changed:
        _save_migrated_pending_kicks(migrated)


def _put_pending_keep_earliest(
    pending: dict[str, PendingKick],
    key: str,
    candidate: PendingKick,
) -> None:
    current = pending.get(key)
    if current is None:
        pending[key] = candidate
        return
    try:
        current_at = from_utc_iso(current.kick_at)
        candidate_at = from_utc_iso(candidate.kick_at)
    except ValueError:
        return
    if candidate_at < current_at:
        pending[key] = candidate


def _label_migration_account_map(
    old_accounts: list[AccountConfig],
    new_accounts: list[AccountConfig],
) -> dict[tuple[str, str], tuple[AccountConfig, AccountConfig]]:
    mapping: dict[tuple[str, str], tuple[AccountConfig, AccountConfig]] = {}
    for old_account, new_account in zip(old_accounts, new_accounts, strict=False):
        if old_account.label != new_account.label:
            mapping[(old_account.label, old_account.provider)] = (old_account, new_account)
    return mapping


def _rename_saved_labels(
    config: Config,
    labels: tuple[str, ...],
) -> tuple[Config, dict[str, str], list[str]]:
    requested = set(labels)
    migrated_accounts, renamed, _skipped = _provider_first_label_migration_accounts(
        config.accounts,
        force_labels=requested,
    )
    not_renamed = sorted(label for label in requested if label not in renamed)
    if not renamed:
        return config, renamed, not_renamed
    updated_schedule = _migrate_schedule_label_keys(config.schedule, renamed)
    updated_config = replace(config, accounts=migrated_accounts, schedule=updated_schedule)
    _write_label_format_config_backup()
    _migrate_label_keyed_status_cache(config.accounts, migrated_accounts, renamed)
    _migrate_label_keyed_pending_kicks(config.accounts, migrated_accounts, renamed)
    updated_config.save()
    return updated_config, renamed, not_renamed


def _migrate_pending_kick_keys(
    old_accounts: list[AccountConfig],
    new_accounts: list[AccountConfig],
) -> None:
    """Rekey pending kicks after setup migrates accounts to canonical identities."""
    if not old_accounts or not new_accounts:
        return
    new_by_label_provider = {
        (account.label, account.provider): account
        for account in new_accounts
    }
    key_map: dict[str, str] = {}
    account_by_new_key: dict[str, AccountConfig] = {}
    for old_account in old_accounts:
        new_account = new_by_label_provider.get((old_account.label, old_account.provider))
        if new_account is None:
            continue
        old_key = account_key_string(old_account)
        new_key = account_key_string(new_account)
        if old_key != new_key:
            key_map[old_key] = new_key
            account_by_new_key[new_key] = new_account
    if not key_map:
        return

    pending = load_pending_kicks(datetime.now(timezone.utc))
    changed = False
    for old_key, new_key in key_map.items():
        existing = pending.pop(old_key, None)
        if existing is None:
            continue
        replacement = pending.get(new_key)
        if replacement is None:
            account = account_by_new_key[new_key]
            existing.account_key = new_key
            existing.account_label = account.label
            existing.provider = account.provider
            pending[new_key] = existing
        changed = True
    if changed:
        _save_migrated_pending_kicks(pending)
