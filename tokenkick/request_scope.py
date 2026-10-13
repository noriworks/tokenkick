"""Bind one run to the reviewed targets, including their pending action identity."""
from contextlib import contextmanager
from contextvars import ContextVar

_scope = ContextVar("approved_run_targets", default=None)


@contextmanager
def run_scope():
    token = _scope.set(None)
    try:
        yield
    finally:
        _scope.reset(token)


def approve_targets(targets):
    _scope.set({t["account_key"]: dict(t) for t in targets})


def target_allowed(key, kick_type, pending):
    scope = _scope.get()
    if scope is None:
        return True
    target = scope.get(key)
    return bool(target and target["kick_type"] == kick_type and target["pending"] == pending)


def consume_target(key):
    scope = _scope.get()
    if scope is not None:
        scope.pop(key, None)
