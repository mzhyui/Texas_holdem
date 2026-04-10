import os
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.actions import router as actions_router
from app.api.bots import router as bots_router
from app.api.games import router as games_router
from app.api.ws import router as ws_router
from app.core import engine as game_engine
from app.database import Base, engine, get_db
from app.models.schemas import SessionRecoveryResponse


@asynccontextmanager
async def lifespan(app: FastAPI):
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield


app = FastAPI(
    title="Texas Hold'em Poker API",
    version="1.0.0",
    description="REST API for managing Texas Hold'em poker game sessions.",
    lifespan=lifespan,
)

app.include_router(games_router)
app.include_router(actions_router)
app.include_router(bots_router)
app.include_router(ws_router)


@app.exception_handler(ValueError)
async def value_error_handler(request: Request, exc: ValueError):
    return JSONResponse(status_code=400, content={"detail": str(exc)})


@app.get("/", tags=["health"])
async def health():
    return {"status": "ok", "service": "texas-poker-server"}


@app.get("/me", response_model=SessionRecoveryResponse, tags=["players"])
async def get_me(
    x_player_token: str = Header(..., alias="X-Player-Token"),
    session: AsyncSession = Depends(get_db),
):
    player = await game_engine.get_player_by_token(session, x_player_token)
    if player is None:
        raise HTTPException(status_code=401, detail="Invalid or missing player token")
    return SessionRecoveryResponse(
        player_id=player.id,
        name=player.name,
        game_id=player.game_id,
        seat=player.seat,
        role=player.role,
        status=player.status,
        chips=player.chips,
    )


# Serve the built Vue SPA (only when dist/ exists — won't break dev mode)
_dist = os.path.join(os.path.dirname(__file__), "..", "frontend", "dist")
if os.path.isdir(_dist):
    app.mount("/", StaticFiles(directory=_dist, html=True), name="static")
