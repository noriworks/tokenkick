"""Identity checks shared by observations and generation dispatch."""


def identity_matches(account, actual) -> bool:
    """Require positive account evidence and reject every known conflicting field."""
    if actual is None:
        return False
    expected = (
        (account.identity_provider_id, getattr(actual, "provider_account_id", None), False),
        (account.identity_email or account.codexbar_account, getattr(actual, "email", None), True),
        (account.identity_org_id, getattr(actual, "organization_id", None), False),
    )
    matched_account = False
    for index, (wanted, observed, fold) in enumerate(expected):
        if not wanted:
            continue
        if observed:
            if (wanted.casefold() if fold else wanted) != (observed.casefold() if fold else observed):
                return False
            if index < 2:
                matched_account = True
        elif index == 2:
            return False
    return matched_account
