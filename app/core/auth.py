from fastapi import Depends, Header, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.db import Player


async def get_current_player(
    game_id: str,
    x_player_token: str = Header(..., alias="X-Player-Token"),
    session: AsyncSession = Depends(get_db),
) -> Player:
    result = await session.execute(
        select(Player).where(Player.token == x_player_token, Player.game_id == game_id)
    )
    player = result.scalar_one_or_none()
    if player is None:
        raise HTTPException(status_code=401, detail="Invalid or missing player token")
    return player


async def require_banker(
    player: Player = Depends(get_current_player),
) -> Player:
    if player.role != "banker":
        raise HTTPException(status_code=403, detail="Banker role required")
    return player
