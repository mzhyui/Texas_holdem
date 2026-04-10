from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import engine as game_engine
from app.core.auth import get_current_player, require_banker
from app.core.ws import manager
from app.database import get_db
from app.models.db import Player
from app.models.schemas import (
    CreateGameRequest,
    CreateGameResponse,
    GameStateResponse,
    HandHistoryResponse,
    HandResponse,
    HandResultsResponse,
    JoinGameRequest,
    JoinGameResponse,
    LeaveResponse,
    LobbyResponse,
    PlayerListResponse,
    SitInResponse,
    SitOutResponse,
    StartGameResponse,
)

router = APIRouter(prefix="/games", tags=["games"])


@router.get("", response_model=LobbyResponse, tags=["lobby"])
async def list_games(
    session: AsyncSession = Depends(get_db),
):
    games = await game_engine.list_games(session)
    return LobbyResponse(games=games)


@router.post("", response_model=CreateGameResponse, status_code=201)
async def create_game(
    req: CreateGameRequest,
    session: AsyncSession = Depends(get_db),
):
    return await game_engine.create_game(session, req)


@router.post("/{game_id}/join", response_model=JoinGameResponse)
async def join_game(
    game_id: str,
    req: JoinGameRequest,
    session: AsyncSession = Depends(get_db),
):
    try:
        return await game_engine.join_game(session, game_id, req)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


async def _push_deal_events(game_id: str, result: StartGameResponse, session: AsyncSession) -> None:
    """Broadcast game state and send each player their private hole cards."""
    await manager.broadcast(game_id, {"type": "game_state", "data": result.game_state.model_dump()})
    for p in result.game_state.players:
        try:
            hand = await game_engine.get_player_hand_by_id(session, game_id, p.player_id)
            await manager.send_to_player(game_id, p.player_id, {"type": "hole_cards", "data": hand.model_dump()})
        except ValueError:
            pass


@router.post("/{game_id}/start", response_model=StartGameResponse)
async def start_game(
    game_id: str,
    banker: Player = Depends(require_banker),
    session: AsyncSession = Depends(get_db),
):
    try:
        result = await game_engine.start_game(session, game_id, banker)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await _push_deal_events(game_id, result, session)
    return result


@router.post("/{game_id}/next-hand", response_model=StartGameResponse)
async def start_next_hand(
    game_id: str,
    banker: Player = Depends(require_banker),
    session: AsyncSession = Depends(get_db),
):
    try:
        result = await game_engine.start_next_hand(session, game_id, banker)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await _push_deal_events(game_id, result, session)
    return result


@router.post("/{game_id}/leave", response_model=LeaveResponse)
async def leave_game(
    game_id: str,
    player: Player = Depends(get_current_player),
    session: AsyncSession = Depends(get_db),
):
    try:
        return await game_engine.leave_game(session, game_id, player)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/{game_id}/sit-out", response_model=SitOutResponse)
async def sit_out(
    game_id: str,
    player: Player = Depends(get_current_player),
    session: AsyncSession = Depends(get_db),
):
    try:
        return await game_engine.sit_out(session, game_id, player)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/{game_id}/sit-in", response_model=SitInResponse)
async def sit_in(
    game_id: str,
    player: Player = Depends(get_current_player),
    session: AsyncSession = Depends(get_db),
):
    try:
        return await game_engine.sit_in(session, game_id, player)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/{game_id}", response_model=GameStateResponse)
async def get_game(
    game_id: str,
    session: AsyncSession = Depends(get_db),
):
    try:
        return await game_engine.get_game_state(session, game_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/{game_id}/hand", response_model=HandResponse)
async def get_hand(
    game_id: str,
    player: Player = Depends(get_current_player),
    session: AsyncSession = Depends(get_db),
):
    try:
        return await game_engine.get_player_hand(session, game_id, player)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/{game_id}/players", response_model=PlayerListResponse)
async def list_players(
    game_id: str,
    session: AsyncSession = Depends(get_db),
):
    try:
        return await game_engine.get_players(session, game_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/{game_id}/history", response_model=HandHistoryResponse)
async def get_history(
    game_id: str,
    session: AsyncSession = Depends(get_db),
):
    rows = await game_engine.get_hand_history(session, game_id)
    return HandHistoryResponse(actions=rows)


@router.get("/{game_id}/results", response_model=HandResultsResponse)
async def get_results(
    game_id: str,
    session: AsyncSession = Depends(get_db),
):
    rows = await game_engine.get_hand_results(session, game_id)
    return HandResultsResponse(results=rows)
