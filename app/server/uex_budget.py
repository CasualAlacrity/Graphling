"""UEX's daily request limit, enforced server-side (settings.uex_daily_call_limit).

Every live UEX call the server makes goes through UEXCorpClient.before_call, which is
spend() here. The count lives in Postgres (uex_call_count), one row per UTC day, so it
holds across restarts and across processes: the API and server/refresh_static_caches.py
share one key and so one limit.

Calls get refused at limit - margin rather than at the limit itself: UEX resets on its
own clock, not necessarily UTC midnight, and get_with_retries can quietly retry a 5xx.
"""
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from db.models import UexCallCount
from db.session import SessionLocal
from server.config import settings
from tools.uexcorp.client import UexCallBudgetExhausted


def _cap() -> int:
    return settings.uex_daily_call_limit - settings.uex_call_margin


async def calls_today() -> int:
    async with SessionLocal() as session:
        result = await session.execute(
            select(UexCallCount.calls).where(UexCallCount.day == datetime.now(UTC).date())
        )
        return result.scalar_one_or_none() or 0


async def remaining_today() -> int:
    return max(0, _cap() - await calls_today())


async def spend(calls: int) -> None:
    """Counts `calls` against today, or raises UexCallBudgetExhausted without counting
    them if that would go past the cap. One statement, so concurrent requests can't both
    squeeze past the last few calls."""
    cap = _cap()
    if calls > cap:
        raise UexCallBudgetExhausted(f"{calls} UEX calls is more than the whole daily cap of {cap}")

    stmt = pg_insert(UexCallCount).values(day=datetime.now(UTC).date(), calls=calls)
    stmt = stmt.on_conflict_do_update(
        index_elements=["day"],
        set_={"calls": UexCallCount.calls + calls},
        where=UexCallCount.calls + calls <= cap,
    ).returning(UexCallCount.calls)

    async with SessionLocal() as session:
        result = await session.execute(stmt)
        counted = result.scalar_one_or_none()
        await session.commit()

    if counted is None:
        raise UexCallBudgetExhausted(f"today's UEX calls are used up ({cap} of {settings.uex_daily_call_limit})")
