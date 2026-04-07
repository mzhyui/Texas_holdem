from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import engine as game_engine
from app.core.auth import get_current_player
from app.core.ws import manager
from app.database import get_db
from app.models.db import Player
from app.models.schemas import ActionResponse, PlayerActionRequest, RebuyResponse

router = APIRouter(prefix="/games", tags=["actions"])


@router.post("/{game_id}/action", response_model=ActionResponse)
async def perform_action(
    game_id: str,
    req: PlayerActionRequest,
    player: Player = Depends(get_current_player),
    session: AsyncSession = Depends(get_db),
):
    try:
        result = await game_engine.process_action(
            session, game_id, player, req.action, req.amount
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    await manager.broadcast(game_id, {
        "type": "action",
        "data": {
            "player_id": player.id,
            "action": result.action,
            "amount": result.amount,
            "pot": result.pot,
            "next_player_id": result.next_player_id,
            "street": result.street,
        },
    })
    game_state = await game_engine.get_game_state(session, game_id)
    await manager.broadcast(game_id, {"type": "game_state", "data": game_state.model_dump()})

    return result


@router.post("/{game_id}/rebuy", response_model=RebuyResponse)
async def rebuy(
    game_id: str,
    player: Player = Depends(get_current_player),
    session: AsyncSession = Depends(get_db),
):
    try:
        return await game_engine.process_rebuy(session, game_id, player)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
