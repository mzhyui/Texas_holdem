from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.ws import manager
from app.database import AsyncSessionLocal
from app.models.db import Player

router = APIRouter(tags=["websocket"])


@router.websocket("/games/{game_id}/ws")
async def websocket_endpoint(
    game_id: str,
    websocket: WebSocket,
    token: str | None = Query(default=None),
) -> None:
    player_id: str | None = None

    if token:
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(Player).where(Player.token == token, Player.game_id == game_id)
            )
            player = result.scalar_one_or_none()
        if player is None:
            await websocket.close(code=4001, reason="Invalid token")
            return
        player_id = player.id

    await manager.connect(game_id, websocket, player_id)
    try:
        while True:
            await websocket.receive_text()  # keep connection alive; messages ignored
    except WebSocketDisconnect:
        manager.disconnect(game_id, websocket)
