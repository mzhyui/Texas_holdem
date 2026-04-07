from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.api.actions import router as actions_router
from app.api.games import router as games_router
from app.api.ws import router as ws_router
from app.database import Base, engine


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
app.include_router(ws_router)


@app.exception_handler(ValueError)
async def value_error_handler(request: Request, exc: ValueError):
    return JSONResponse(status_code=400, content={"detail": str(exc)})


@app.get("/", tags=["health"])
async def health():
    return {"status": "ok", "service": "texas-poker-server"}
