"""Guards the Phase 2 decision that UexPriceCache/UexReferenceCacheRecord stay global,
shared economy data — not a design that needs re-litigating every time someone's near
the trade ledger's user_id (docs/todo.md's "Cache + reference tables stay global"). A
column check rather than a comment: a future edit that adds user_id to either cache
table breaks this test instead of drifting in silently.
"""
from db.models import TradeLeg, TradeRun, UexPriceCache, UexReferenceCacheRecord


def test_uex_price_cache_has_no_tenant_column():
    assert "user_id" not in UexPriceCache.__table__.columns.keys()


def test_uex_reference_cache_has_no_tenant_column():
    assert "user_id" not in UexReferenceCacheRecord.__table__.columns.keys()


def test_trade_run_and_leg_do_have_a_tenant_column():
    # The other half of the same boundary — the ledger tables are exactly the ones that
    # should be scoped, so this fails loudly if user_id ever gets removed from them too.
    assert "user_id" in TradeRun.__table__.columns.keys()
    assert "user_id" in TradeLeg.__table__.columns.keys()
