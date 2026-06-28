"""Account discovery and account-list shaping for TokenKick CLI."""

from __future__ import annotations

import json
import re
import shutil
from dataclasses import replace
from pathlib import Path

from .antigravity import (
    antigravity_cli_app_dir,
    antigravity_cli_detected,
    read_antigravity_cli_identity,
)
from .claude_setup import ensure_claude_cli_settings, ensure_claude_probe_ready
from .codexbar_source import (
    _codexbar_all_account_entries,
    _codexbar_entry_email,
    _codexbar_entry_provider,
    _codexbar_json_entries,
    _load_codexbar_all_accounts_json,
    _load_codexbar_legacy_json,
    _parse_codexbar_json,
)
from .direct import email_from_id_token
from .kicker import KICKABLE_PROVIDERS
from .migrations import (
    _codex_direct_readable,
    _provider_first_label,
    _unreadable_codex_direct_duplicate_exists,
    _volatile_primary_codex_duplicate_exists,
)
from .models import (
    CODEX_DEFAULT_RATE_LIMIT_ID,
    CODEX_SPARK_MODEL_ID,
    AccountConfig,
    AccountState,
    AccountStatus,
    Config,
    DataSource,
    account_key,
    account_key_string,
    merge_discovered_account,
)
from .sources import (
    _read_codex_appserver_ratelimits_for_account,
    codex_appserver_bucket_metadata,
    codex_appserver_spark_bucket,
    polling_pass_cache,
)


def _cli():
    from . import cli as cli_mod

    return cli_mod


def _load_account_status_pairs(
    config: Config,
    *,
    prepare_claude_setup: bool = False,
) -> tuple[list[AccountConfig], list[AccountStatus], bool, str, list[AccountConfig]]:
    """Load account/status pairs, preferring fresh discovery metadata."""
    with polling_pass_cache():
        return _load_account_status_pairs_cached(
            config,
            prepare_claude_setup=prepare_claude_setup,
        )


def _load_account_status_pairs_cached(
    config: Config,
    *,
    prepare_claude_setup: bool = False,
) -> tuple[list[AccountConfig], list[AccountStatus], bool, str, list[AccountConfig]]:
    try:
        discovered_accounts, discovered_statuses, summary = _cli()._discover_accounts_and_statuses(
            config,
            prepare_claude_setup=prepare_claude_setup,
        )
    except TypeError:
        discovered_accounts, discovered_statuses, summary = _cli()._discover_accounts_and_statuses()
    if not config.accounts:
        return discovered_accounts, discovered_statuses, True, summary, []

    discovered_by_key: dict[tuple[str, str, str], tuple[AccountConfig, AccountStatus]] = {}
    for account, status in zip(discovered_accounts, discovered_statuses, strict=False):
        discovered_by_key[_account_key(account)] = (account, status)
        if (
            account.provider == "codex"
            and account.identity_email
            and (account.codex_rate_limit_id in {None, CODEX_DEFAULT_RATE_LIMIT_ID})
            and _codex_direct_readable(account, status)
        ):
            discovered_by_key[("account", "codex", account.identity_email.lower())] = (
                account,
                status,
            )
        if account.provider == "claude" and account.source == DataSource.CLAUDE_DIRECT:
            discovered_by_key[("codexbar-cli", "claude", "claude")] = (account, status)
            if account.identity_email:
                discovered_by_key[("codexbar-cli", "claude", account.identity_email.lower())] = (
                    account,
                    status,
                )
        if account.provider == "antigravity" and account.source == DataSource.ANTIGRAVITY_CLI:
            discovered_by_key[("codexbar-cli", "antigravity", "antigravity")] = (
                account,
                status,
            )
            if account.identity_email:
                discovered_by_key[
                    ("codexbar-cli", "antigravity", account.identity_email.lower())
                ] = (account, status)
    configured_pairs = [
        _pair_for_configured_account(account, discovered_by_key, config)
        for account in config.accounts
    ]
    existing_keys = {_account_key(account) for account, _status in configured_pairs}
    new_pairs = [
        (account, status)
        for account, status in zip(discovered_accounts, discovered_statuses, strict=False)
        if _account_key(account) not in existing_keys
        and not _unreadable_codex_direct_duplicate_exists(account, status, existing_keys)
        and not _volatile_primary_codex_duplicate_exists(
            account,
            [configured_account for configured_account, _status in configured_pairs],
        )
    ]
    configured_spark_pairs = _spark_pairs_for_configured_codex_homes(
        configured_pairs,
        existing_keys | {_account_key(account) for account, _status in new_pairs},
    )
    new_pairs.extend(configured_spark_pairs)
    all_pairs = configured_pairs + new_pairs
    all_accounts, all_statuses = _apply_display_labels(
        all_pairs,
        preserve_count=len(config.accounts),
    )
    _cli()._prune_phantom_session_observations_for_accounts(all_accounts)
    new_accounts = all_accounts[len(config.accounts):]
    return all_accounts, all_statuses, False, _format_configured_accounts_summary(all_accounts), new_accounts


def _discover_accounts_and_statuses(
    config: Config | None = None,
    *,
    prepare_claude_setup: bool = False,
) -> tuple[list[AccountConfig], list[AccountStatus], str]:
    """Discover local rate-limit sources without writing config."""
    discovered: list[tuple[AccountConfig, AccountStatus]] = []
    codex_all_accounts_available = False

    _cli()._setup_progress("Discovering local account homes")
    try:
        direct_accounts, direct_statuses = _cli()._discover_direct_accounts(
            config,
            prepare_claude_setup=prepare_claude_setup,
        )
    except TypeError:
        direct_accounts, direct_statuses = _cli()._discover_direct_accounts()
    direct_providers = {account.provider for account in direct_accounts}
    if direct_accounts:
        discovered.extend(zip(direct_accounts, direct_statuses, strict=False))

    if shutil.which("codexbar"):
        _cli()._setup_progress("Checking account snapshots")
        accounts, statuses, codex_all_accounts_available = _discover_codexbar_accounts()
        if accounts:
            for account, status in zip(accounts, statuses, strict=False):
                if account.provider == "claude" and "claude" in direct_providers:
                    continue
                discovered.append((account, status))

    if not codex_all_accounts_available and "codex" not in direct_providers:
        _cli()._setup_progress("Checking session-file fallbacks")
        session_accounts, session_statuses = _cli()._discover_codex_session_accounts()
        discovered.extend(zip(session_accounts, session_statuses, strict=False))

    if not discovered:
        return [], [], "No direct provider identity, CodexBar CLI, or Codex session files found."

    _cli()._setup_progress("Merging discovered accounts")
    accounts, statuses = _merge_discovered_accounts(discovered)
    return accounts, statuses, _format_discovery_summary(accounts, "auto-discovery")


def _discover_direct_accounts(
    config: Config | None = None,
    *,
    prepare_claude_setup: bool = False,
) -> tuple[list[AccountConfig], list[AccountStatus]]:
    """Discover direct Codex and Claude accounts without writing config."""
    accounts: list[AccountConfig] = []
    statuses: list[AccountStatus] = []
    _cli()._setup_progress("Checking direct provider homes")
    _append_codex_direct_accounts(accounts, statuses)
    _append_claude_direct_account(
        accounts,
        statuses,
        config,
        prepare_claude_setup=prepare_claude_setup,
    )
    _append_antigravity_cli_account(accounts, statuses)
    return accounts, statuses


def _append_codex_direct_accounts(
    accounts: list[AccountConfig],
    statuses: list[AccountStatus],
) -> None:
    seen_homes: set[str] = set()

    primary_home = Path.home() / ".codex"
    _append_codex_direct_account(accounts, statuses, primary_home)
    seen_homes.add(str(primary_home))

    tokenkick_homes = _tokenkick_managed_codex_homes()
    for codex_home in tokenkick_homes:
        home_key = str(codex_home)
        if home_key in seen_homes:
            continue
        seen_homes.add(home_key)
        _append_codex_direct_account(accounts, statuses, codex_home)

    managed_path = (
        Path.home()
        / "Library"
        / "Application Support"
        / "CodexBar"
        / "managed-codex-accounts.json"
    )
    if not managed_path.exists():
        return
    try:
        data = json.loads(managed_path.read_text())
    except (json.JSONDecodeError, OSError):
        return

    for entry in data.get("accounts", []):
        if not isinstance(entry, dict):
            continue
        managed_home = entry.get("managedHomePath")
        if not isinstance(managed_home, str) or not managed_home:
            continue
        if managed_home in seen_homes:
            continue
        seen_homes.add(managed_home)
        _append_codex_direct_account(
            accounts,
            statuses,
            Path(managed_home),
            managed_email=entry.get("email") if isinstance(entry.get("email"), str) else None,
            managed_provider_id=(
                entry.get("providerAccountID")
                if isinstance(entry.get("providerAccountID"), str)
                else None
            ),
        )


def _tokenkick_managed_codex_homes() -> list[Path]:
    homes_dir = Path(_cli().CONFIG_DIR) / "codex-homes"
    if not homes_dir.is_dir():
        return []
    try:
        children = sorted(homes_dir.iterdir(), key=lambda path: path.name)
    except OSError:
        return []
    return [path for path in children if path.is_dir()]


def _append_codex_direct_account(
    accounts: list[AccountConfig],
    statuses: list[AccountStatus],
    codex_home: Path,
    *,
    managed_email: str | None = None,
    managed_provider_id: str | None = None,
) -> None:
    identity = _cli().read_codex_identity(codex_home)
    provider_id = identity.provider_account_id if identity else managed_provider_id
    email = identity.email if identity else managed_email
    if not provider_id and not email:
        return
    label = _label_from_email(email) if email else "codex-primary"
    sessions_dir = codex_home / "sessions"
    account = AccountConfig(
        label=label,
        provider="codex",
        source=DataSource.CODEX_DIRECT,
        auto_kick=True,
        session_auto_kick=True,
        session_path=str(sessions_dir),
        provider_home=str(codex_home),
        codexbar_account=email,
        identity_provider_id=provider_id,
        identity_email=email,
        label_origin="auto",
    )
    accounts.append(account)
    _cli()._setup_progress("Reading provider status")
    statuses.append(_cli().fetch_status(account))
    _append_codex_spark_account_if_available(
        accounts,
        statuses,
        account,
        codex_home,
    )


def _append_codex_spark_account_if_available(
    accounts: list[AccountConfig],
    statuses: list[AccountStatus],
    base_account: AccountConfig,
    codex_home: Path,
) -> None:
    bucket = codex_appserver_spark_bucket(codex_appserver_bucket_metadata(codex_home))
    if bucket is None:
        return
    limit_id = str(bucket.get("limit_id") or bucket.get("key") or "").strip()
    if not limit_id or limit_id == CODEX_DEFAULT_RATE_LIMIT_ID:
        return
    spark_account = _codex_spark_account_from_bucket(base_account, bucket)
    accounts.append(spark_account)
    statuses.append(_read_codex_appserver_ratelimits_for_account(spark_account, codex_home))


def _spark_pairs_for_configured_codex_homes(
    configured_pairs: list[tuple[AccountConfig, AccountStatus]],
    existing_keys: set[tuple[str, str, str]],
) -> list[tuple[AccountConfig, AccountStatus]]:
    pairs: list[tuple[AccountConfig, AccountStatus]] = []
    for account, _status in configured_pairs:
        if not _configured_codex_home_can_seed_spark(account):
            continue
        codex_home = Path(account.provider_home or "")
        bucket = codex_appserver_spark_bucket(codex_appserver_bucket_metadata(codex_home))
        if bucket is None:
            continue
        spark_account = _codex_spark_account_from_bucket(account, bucket)
        if _account_key(spark_account) in existing_keys:
            continue
        pairs.append(
            (
                spark_account,
                _read_codex_appserver_ratelimits_for_account(spark_account, codex_home),
            )
        )
        existing_keys.add(_account_key(spark_account))
    return pairs


def _configured_codex_home_can_seed_spark(account: AccountConfig) -> bool:
    return (
        account.provider == "codex"
        and account.source == DataSource.CODEX_DIRECT
        and bool(account.provider_home)
        and account.codex_rate_limit_id in {None, CODEX_DEFAULT_RATE_LIMIT_ID}
    )


def _codex_spark_account_from_bucket(
    base_account: AccountConfig,
    bucket: dict[str, str | None],
) -> AccountConfig:
    limit_id = str(bucket.get("limit_id") or bucket.get("key") or "").strip()
    limit_name = str(bucket.get("limit_name") or bucket.get("display_name") or "").strip() or None
    return replace(
        base_account,
        label=f"codex-spark ({_codex_spark_label_component(base_account)})",
        codex_rate_limit_id=limit_id,
        codex_rate_limit_name=limit_name,
        auto_kick=False,
        weekly_auto_kick=False,
        session_auto_kick=False,
        kick_model=CODEX_SPARK_MODEL_ID,
        plan_tier=base_account.plan_tier or "spark",
        usable_session_minutes=None,
        label_origin="auto",
    )


def _codex_spark_label_component(account: AccountConfig) -> str:
    base = _display_base_label(account)
    match = re.fullmatch(r"codex \((.+)\)", base)
    if match:
        return _sanitize_label(match.group(1))
    return base


def _append_claude_direct_account(
    accounts: list[AccountConfig],
    statuses: list[AccountStatus],
    config: Config | None = None,
    *,
    prepare_claude_setup: bool = False,
) -> None:
    identity = _cli().read_claude_identity()
    if identity is None:
        return
    if prepare_claude_setup:
        ensure_claude_probe_ready(_cli().CONFIG_DIR)
        ensure_claude_cli_settings(Path.home())
    label = _label_from_email(identity.email) if identity.email else "claude"
    account = AccountConfig(
        label=label,
        provider="claude",
        source=DataSource.CLAUDE_DIRECT,
        auto_kick=True,
        session_auto_kick=True,
        identity_provider_id=identity.provider_account_id,
        identity_email=identity.email,
        identity_org_id=identity.organization_id,
        label_origin="auto",
    )
    accounts.append(account)
    _cli()._setup_progress("Reading provider status")
    status = _cli()._fetch_status(account, config) if config is not None else _cli().fetch_status(account)
    statuses.append(status)


def _append_antigravity_cli_account(
    accounts: list[AccountConfig],
    statuses: list[AccountStatus],
) -> None:
    """Discover an Antigravity CLI monitor account without requiring CodexBar."""
    if not antigravity_cli_detected():
        return
    email = read_antigravity_cli_identity()
    label = _label_from_email(email) or "antigravity"
    account = AccountConfig(
        label=label,
        provider="antigravity",
        source=DataSource.ANTIGRAVITY_CLI,
        auto_kick=False,
        weekly_auto_kick=False,
        session_auto_kick=False,
        provider_home=str(antigravity_cli_app_dir()),
        identity_email=email,
        codexbar_account=email,
        label_origin="auto",
    )
    accounts.append(account)
    _cli()._setup_progress("Reading provider status")
    statuses.append(_cli().fetch_status(account))


def _discover_codex_session_accounts() -> tuple[list[AccountConfig], list[AccountStatus]]:
    """Discover primary and CodexBar-managed Codex session homes."""
    accounts: list[AccountConfig] = []
    statuses: list[AccountStatus] = []

    primary_home = Path.home() / ".codex"
    primary_email = _primary_codex_email(primary_home)
    primary_label = _label_from_email(primary_email) if primary_email else "codex-primary"
    primary_sessions_dir = primary_home / "sessions"
    if primary_sessions_dir.exists() or primary_email:
        _append_codex_session_account(
            accounts,
            statuses,
            label=primary_label,
            email=primary_email,
            sessions_dir=primary_sessions_dir,
        )

    managed_path = (
        Path.home()
        / "Library"
        / "Application Support"
        / "CodexBar"
        / "managed-codex-accounts.json"
    )
    if not managed_path.exists():
        return accounts, statuses

    try:
        data = json.loads(managed_path.read_text())
    except (json.JSONDecodeError, OSError):
        return accounts, statuses

    for entry in data.get("accounts", []):
        if not isinstance(entry, dict):
            continue
        email = entry.get("email")
        managed_home = entry.get("managedHomePath")
        if not email or not managed_home:
            continue

        _append_codex_session_account(
            accounts,
            statuses,
            label=_label_from_email(email),
            email=email,
            sessions_dir=Path(managed_home) / "sessions",
        )

    return accounts, statuses


def _append_codex_session_account(
    accounts: list[AccountConfig],
    statuses: list[AccountStatus],
    label: str,
    email: str | None,
    sessions_dir: Path,
) -> None:
    account = AccountConfig(
        label=label,
        provider="codex",
        source=DataSource.CODEX_SESSION_FILE,
        auto_kick=True,
        session_auto_kick=True,
        session_path=str(sessions_dir),
        codexbar_account=email,
        label_origin="auto",
    )
    accounts.append(account)

    if sessions_dir.exists():
        statuses.append(_cli().fetch_status(account))
        return

    statuses.append(
        AccountStatus(
            label=label,
            state=AccountState.UNKNOWN,
            error="No session data — use this account to start tracking.",
        )
    )


def _primary_codex_email(codex_home: Path) -> str | None:
    auth_path = codex_home / "auth.json"
    if not auth_path.exists():
        return None

    try:
        data = json.loads(auth_path.read_text())
    except (json.JSONDecodeError, OSError):
        return None

    id_token = (data.get("tokens") or {}).get("id_token")
    if not isinstance(id_token, str):
        return None
    return email_from_id_token(id_token)


def _discover_codexbar_accounts() -> tuple[list[AccountConfig], list[AccountStatus], bool]:
    """Run CodexBar and turn usage entries into tracked accounts."""
    accounts: list[AccountConfig] = []
    statuses: list[AccountStatus] = []
    used_labels: set[str] = set()
    codex_all_accounts_available = False

    all_accounts_data, _all_accounts_error = _load_codexbar_all_accounts_json()
    all_account_entries = _codexbar_all_account_entries(all_accounts_data)
    valid_all_account_entries = [
        entry for entry in all_account_entries if isinstance(entry.get("usage"), dict)
    ]
    if valid_all_account_entries:
        codex_all_accounts_available = True
        for entry in all_account_entries:
            _append_codexbar_account_entry(
                accounts,
                statuses,
                used_labels,
                entry,
                include_account_errors=True,
            )

    legacy_data, _legacy_error = _load_codexbar_legacy_json()
    for entry in _codexbar_json_entries(legacy_data):
        provider = _codexbar_provider(entry)
        if codex_all_accounts_available and provider == "codex":
            continue
        _append_codexbar_account_entry(accounts, statuses, used_labels, entry)

    return accounts, statuses, codex_all_accounts_available


def _append_codexbar_account_entry(
    accounts: list[AccountConfig],
    statuses: list[AccountStatus],
    used_labels: set[str],
    entry: dict,
    include_account_errors: bool = False,
) -> None:
    if not isinstance(entry.get("usage"), dict):
        has_account_error = (
            include_account_errors
            and isinstance(entry.get("error"), dict)
            and _codexbar_email(entry)
        )
        if not has_account_error:
            return

    provider = _codexbar_provider(entry)
    email = _codexbar_email(entry)
    label = _unique_label(_codexbar_label(entry), used_labels)
    account = AccountConfig(
        label=label,
        provider=provider,
        source=DataSource.CODEXBAR_CLI,
        auto_kick=provider in KICKABLE_PROVIDERS,
        session_auto_kick=provider in KICKABLE_PROVIDERS,
        codexbar_provider=provider,
        codexbar_account=email,
        label_origin="auto",
    )
    status = _parse_codexbar_json(label, entry, provider=provider, account=email)
    if status.observed_at is None:
        status.observed_at = _cli()._status_cache_observed_at()
    if status.source_detail is None:
        status.source_detail = "codexbar-cli"

    accounts.append(account)
    statuses.append(status)


def _codexbar_provider(entry: dict) -> str:
    return _codexbar_entry_provider(entry) or "account"


def _codexbar_email(entry: dict) -> str | None:
    return _codexbar_entry_email(entry)


def _codexbar_label(entry: dict) -> str:
    provider = _codexbar_provider(entry)
    email = _codexbar_email(entry)
    return _label_from_email(email) if email else _sanitize_label(provider)


def _label_from_email(email: str | None) -> str:
    if not email:
        return ""
    return _sanitize_label(email.split("@", 1)[0])


def _sanitize_label(value: str) -> str:
    label = re.sub(r"[^a-zA-Z0-9_.-]+", "-", value.lower()).strip("-")
    return label or "account"


def _unique_label(label: str, used_labels: set[str]) -> str:
    if label not in used_labels:
        used_labels.add(label)
        return label

    index = 2
    while f"{label}-{index}" in used_labels:
        index += 1
    unique = f"{label}-{index}"
    used_labels.add(unique)
    return unique


def _format_discovery_summary(accounts: list[AccountConfig], source: str) -> str:
    providers = sorted({account.provider for account in accounts})
    provider_text = ", ".join(providers)
    count = len(accounts)
    noun = "account" if count == 1 else "accounts"
    return f"Found {count} {noun} via {source}: {provider_text}."


def _format_configured_accounts_summary(accounts: list[AccountConfig]) -> str:
    counts: dict[str, int] = {}
    for account in accounts:
        key = _summary_account_kind(account)
        counts[key] = counts.get(key, 0) + 1
    count = len(accounts)
    noun = "account" if count == 1 else "accounts"
    parts = [f"{kind}={counts[kind]}" for kind in sorted(counts)]
    return f"Configured {count} {noun} after discovery: {', '.join(parts)}."


def _summary_account_kind(account: AccountConfig) -> str:
    if (
        account.provider == "codex"
        and account.codex_rate_limit_id
        and account.codex_rate_limit_id != CODEX_DEFAULT_RATE_LIMIT_ID
    ):
        return "codex-spark"
    return account.provider


def _account_key(account: AccountConfig) -> tuple[str, str, str]:
    return account_key(account)


def _discovery_identity_aliases(
    discovered: list[tuple[AccountConfig, AccountStatus]],
) -> dict[tuple[str, str, str], tuple[str, str, str]]:
    aliases: dict[tuple[str, str, str], tuple[str, str, str]] = {}
    for account, status in discovered:
        canonical = _account_key(account)
        if (
            account.provider == "codex"
            and account.identity_email
            and (account.codex_rate_limit_id in {None, CODEX_DEFAULT_RATE_LIMIT_ID})
            and _codex_direct_readable(account, status)
        ):
            aliases[("account", "codex", account.identity_email.lower())] = canonical
        elif (
            account.provider == "codex"
            and account.identity_email
            and (account.codex_rate_limit_id in {None, CODEX_DEFAULT_RATE_LIMIT_ID})
            and not _codex_has_home_scope(account)
        ):
            aliases[canonical] = ("account", "codex", account.identity_email.lower())
        if account.provider == "claude" and account.source == DataSource.CLAUDE_DIRECT:
            aliases[("codexbar-cli", "claude", "claude")] = canonical
            if account.identity_email:
                aliases[("codexbar-cli", "claude", account.identity_email.lower())] = canonical
        if account.provider == "antigravity" and account.source == DataSource.ANTIGRAVITY_CLI:
            aliases[("codexbar-cli", "antigravity", "antigravity")] = canonical
            if account.identity_email:
                aliases[("codexbar-cli", "antigravity", account.identity_email.lower())] = canonical
    return aliases


def _discovery_key(
    account: AccountConfig,
    aliases: dict[tuple[str, str, str], tuple[str, str, str]],
) -> tuple[str, str, str]:
    key = _account_key(account)
    return aliases.get(key, key)


def _codex_has_home_scope(account: AccountConfig) -> bool:
    return bool(
        account.provider == "codex"
        and account.source == DataSource.CODEX_DIRECT
        and (account.provider_home or account.session_path)
    )


def _phantom_session_key(account: AccountConfig) -> str:
    return account_key_string(account)


def _merge_discovered_accounts(
    discovered: list[tuple[AccountConfig, AccountStatus]],
) -> tuple[list[AccountConfig], list[AccountStatus]]:
    merged: dict[tuple[str, str, str], tuple[AccountConfig, AccountStatus]] = {}
    order: list[tuple[str, str, str]] = []
    aliases = _discovery_identity_aliases(discovered)

    for account, status in discovered:
        key = _discovery_key(account, aliases)
        current = merged.get(key)
        if current is None:
            merged[key] = (account, status)
            order.append(key)
            continue
        if _discovery_score(account, status) > _discovery_score(current[0], current[1]):
            merged[key] = (account, status)

    return _apply_display_labels([merged[key] for key in order])


def _apply_display_labels(
    pairs: list[tuple[AccountConfig, AccountStatus]],
    *,
    preserve_count: int = 0,
) -> tuple[list[AccountConfig], list[AccountStatus]]:
    accounts = [account for account, _status in pairs]
    statuses = [status for _account, status in pairs]
    used_labels: set[str] = set()
    labeled_accounts: list[AccountConfig] = []
    labeled_statuses: list[AccountStatus] = []
    for index, (account, status) in enumerate(zip(accounts, statuses, strict=False)):
        if index < preserve_count:
            labeled_accounts.append(account)
            labeled_statuses.append(_replace_status_label(status, account.label))
            used_labels.add(account.label)
            continue
        base_label = _display_base_label(account)
        label = _provider_first_label(account) or base_label
        unique_label = _unique_label(label, used_labels)
        labeled_accounts.append(replace(account, label=unique_label, label_origin="auto"))
        labeled_statuses.append(_replace_status_label(status, unique_label))
    return labeled_accounts, labeled_statuses


def _replace_status_label(status: AccountStatus, label: str) -> AccountStatus:
    updated = replace(status, label=label)
    claude_probe_context = getattr(status, "_claude_probe_context", None)
    if claude_probe_context is not None:
        setattr(updated, "_claude_probe_context", claude_probe_context)
    return updated


def _display_base_label(account: AccountConfig) -> str:
    if account.identity_email:
        return _label_from_email(account.identity_email)
    if account.codexbar_account:
        return _label_from_email(account.codexbar_account)
    return account.label


def _discovery_score(account: AccountConfig, status: AccountStatus) -> int:
    score = _status_detail_score(status)
    if account.source == DataSource.CODEX_DIRECT:
        if _codex_direct_readable(account, status):
            score += 10
    elif account.source == DataSource.CLAUDE_DIRECT:
        score += 10
    elif account.provider == "codex" and account.source == DataSource.CODEX_SESSION_FILE:
        score += 3
    return score


def _status_detail_score(status: AccountStatus) -> int:
    score = 0
    if status.state != AccountState.UNKNOWN:
        score += 1
    if status.used_percent is not None:
        score += 4
    if status.window_minutes is not None:
        score += 2
    if status.resets_in_seconds is not None:
        score += 1
    if status.quota_windows:
        score += 4
    return score


def _pair_for_configured_account(
    account: AccountConfig,
    discovered_by_key: dict[tuple[str, str, str], tuple[AccountConfig, AccountStatus]],
    config: Config | None = None,
) -> tuple[AccountConfig, AccountStatus]:
    discovered = discovered_by_key.get(_account_key(account))
    if discovered is not None:
        discovered_account, discovered_status = discovered
        merged_account = merge_discovered_account(account, discovered_account)
        if _claude_identity_only_discovery_status(discovered_account, discovered_status):
            fallback_status = _cached_configured_status(account) or _cli()._fetch_status(account, config)
            if fallback_status.state != AccountState.UNKNOWN:
                return merged_account, _replace_status_label(fallback_status, account.label)
        if (
            discovered_status.state == AccountState.UNKNOWN
            and discovered_account.source not in {DataSource.CODEX_DIRECT, DataSource.CLAUDE_DIRECT}
        ):
            fallback_status = _cli()._fetch_status(account, config)
            if fallback_status.state != AccountState.UNKNOWN:
                return merged_account, _replace_status_label(fallback_status, account.label)
        return merged_account, _replace_status_label(discovered_status, account.label)
    return account, _cli()._fetch_status(account, config)


def _claude_identity_only_discovery_status(
    account: AccountConfig,
    status: AccountStatus,
) -> bool:
    return (
        account.provider == "claude"
        and account.source == DataSource.CLAUDE_DIRECT
        and status.state == AccountState.UNKNOWN
        and isinstance(status.error, str)
        and status.error.startswith("Claude identity was read from ~/.claude.json")
    )


def _cached_configured_status(account: AccountConfig) -> AccountStatus | None:
    entries = _cli()._load_status_cache_entries()
    entry = entries.get(account_key_string(account))
    if not isinstance(entry, dict):
        return None
    if not _cli()._status_cache_entry_matches_configured_account(account, entry):
        return None
    status = entry.get("status")
    if not isinstance(status, AccountStatus) or status.state == AccountState.UNKNOWN:
        return None
    return _replace_status_label(status, account.label)


def _format_new_account_note(accounts: list[AccountConfig]) -> str:
    count = len(accounts)
    noun = "account" if count == 1 else "accounts"
    labels = ", ".join(account.label for account in accounts)
    return f"{count} new {noun} discovered: {labels}. Run tk setup to save."


def _setup_footer(config: Config) -> str:
    if config.notifications.enabled:
        return "Run tk setup to save this config."
    return "Run tk setup to save this config and enable notifications."
