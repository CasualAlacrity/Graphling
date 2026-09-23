from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException

from db.models import User
from ledger_schemas import CreateRunRequest, RecordTransactionRequest, TradeLegOut, TradeRunOut
from server import ledger_service
from server.dependencies import get_current_user

router = APIRouter(prefix="/trade-runs", tags=["trade-runs"])


@router.post("", response_model=TradeRunOut)
async def create_run(body: CreateRunRequest, user: User = Depends(get_current_user)):
    run = await ledger_service.create_run_from_route(body.route, body.quantity_scu, body.ship, user.id)
    return run


@router.get("/in-progress", response_model=list[TradeRunOut])
async def list_in_progress_runs(user: User = Depends(get_current_user)):
    return await ledger_service.get_in_progress_runs(user.id)


@router.get("/finalized", response_model=list[TradeRunOut])
async def list_finalized_runs(limit: int = 50, user: User = Depends(get_current_user)):
    return await ledger_service.get_finalized_runs(user.id, limit)


@router.post("/{run_id}/finalize", response_model=TradeRunOut)
async def finalize_run(run_id: UUID, user: User = Depends(get_current_user)):
    try:
        return await ledger_service.finalize_run(run_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.delete("/{run_id}", status_code=204)
async def delete_run(run_id: UUID, user: User = Depends(get_current_user)):
    await ledger_service.delete_run(run_id)


@router.post("/legs/{leg_id}/advance", response_model=TradeLegOut)
async def advance_leg(leg_id: UUID, user: User = Depends(get_current_user)):
    try:
        return await ledger_service.advance_leg(leg_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/legs/{leg_id}/purchase", response_model=TradeLegOut)
async def record_purchase(leg_id: UUID, body: RecordTransactionRequest, user: User = Depends(get_current_user)):
    try:
        return await ledger_service.record_purchase(
            leg_id, body.quantity_scu, body.price_per_unit, body.cargo_transfer_type, body.cargo_transfer_fee,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/legs/{leg_id}/sale", response_model=TradeLegOut)
async def record_sale(leg_id: UUID, body: RecordTransactionRequest, user: User = Depends(get_current_user)):
    try:
        return await ledger_service.record_sale(
            leg_id, body.quantity_scu, body.price_per_unit, body.cargo_transfer_type, body.cargo_transfer_fee,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
