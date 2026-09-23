"""The trade ledger's pilot-scoping identity for this process.

Multi-tenancy here is "shared backend, per-user client" (docs/todo.md Phase 2) — one
ALICE process is always exactly one pilot for its whole lifetime, not a server handling
requests for many. That's why this is a single process-wide value set once, right after
Discord login resolves and the local users row is resolved/created (voice/__init__.py,
db/user_service.get_or_create_user), rather than something threaded through every tool
call and graph state: there's no notion of "switching pilots" mid-process to thread
through in the first place.

Holds the local users.id (a UUID), not the raw Discord id — that indirection is what
TradeRun/TradeLeg.user_id actually FKs to (see db/models.py's User docstring).
"""
import uuid

_current_user_id: uuid.UUID | None = None


def set_current_user_id(user_id: uuid.UUID) -> None:
    global _current_user_id
    _current_user_id = user_id


def get_current_user_id() -> uuid.UUID:
    if _current_user_id is None:
        raise RuntimeError(
            "No pilot identity set yet — set_current_user_id() must run after login "
            "before anything touches the trade ledger."
        )
    return _current_user_id
