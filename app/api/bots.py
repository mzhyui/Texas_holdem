"""
In-process bot management API.

POST   /games/{game_id}/bots           — spawn a bot that joins and plays
GET    /games/{game_id}/bots           — list active bots for this game
DELETE /games/{game_id}/bots/{bot_id}  — kick a bot (leave + cancel task)

Bots run as asyncio Tasks. Blocking HTTP calls are offloaded via
asyncio.to_thread so they don't stall the event loop.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass

import requests as _requests
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.bot.card_util import GameSnapshot, build_snapshot, legal_actions
from app.bot.config import VALID_STYLES, Config
from app.bot.heuristic_util import heuristic_decision
from app.bot.llm_util import LLM_SYSTEM, build_llm_prompt, history_snippet

LOG = logging.getLogger("poker_bot")

router = APIRouter(prefix="/games", tags=["bots"])

# ---------------------------------------------------------------------------
# In-memory bot registry  {game_id: {bot_id: BotEntry}}
# ---------------------------------------------------------------------------

@dataclass
class BotEntry:
    bot_id: str
    name: str
    style: str
    llm_enabled: bool
    task: asyncio.Task
    token: str = ""
    player_id: str = ""
    _last_hand: int = -1


_bots: dict[str, dict[str, BotEntry]] = {}


# ---------------------------------------------------------------------------
# Request / response schemas
# ---------------------------------------------------------------------------

class AddBotRequest(BaseModel):
    name: str = "PokerBot"
    style: str = "mild"
    llm_endpoint: str = ""
    llm_api_key: str = ""
    llm_model: str = "gpt-4o-mini"


class BotInfo(BaseModel):
    bot_id: str
    name: str
    style: str
    llm_enabled: bool
    player_id: str


class BotListResponse(BaseModel):
    bots: list[BotInfo]


class AddBotResponse(BaseModel):
    bot_id: str
    name: str


# ---------------------------------------------------------------------------
# Blocking HTTP helpers (run via asyncio.to_thread)
# ---------------------------------------------------------------------------

def _get(sess: _requests.Session, base: str, path: str, token: str | None = None) -> dict:
    headers = {"X-Player-Token": token} if token else {}
    r = sess.get(f"{base}{path}", headers=headers, timeout=10)
    r.raise_for_status()
    return r.json()


def _post(sess: _requests.Session, base: str, path: str, body: dict | None = None, token: str | None = None) -> dict:
    headers: dict = {"Content-Type": "application/json"}
    if token:
        headers["X-Player-Token"] = token
    r = sess.post(f"{base}{path}", json=body or {}, headers=headers, timeout=10)
    r.raise_for_status()
    return r.json()


# ---------------------------------------------------------------------------
# LLM call (blocking, run via to_thread)
# ---------------------------------------------------------------------------

def _llm_call_sync(cfg: Config, snap: GameSnapshot, snippet: str):
    """Blocking LLM HTTP call. Returns Decision or None."""
    import json
    from app.bot.core import Decision

    if not cfg.llm_enabled or not cfg.llm_api_key:
        return None

    prompt = build_llm_prompt(snap, snippet, cfg.style)
    url = f"{cfg.llm_base_url}/chat/completions"
    headers = {"Authorization": f"Bearer {cfg.llm_api_key}", "Content-Type": "application/json"}
    body = {
        "model": cfg.llm_model,
        "temperature": cfg.llm_temperature,
        "messages": [
            {"role": "system", "content": LLM_SYSTEM},
            {"role": "user", "content": prompt},
        ],
    }

    try:
        r = _requests.post(url, json=body, headers=headers, timeout=cfg.llm_timeout)
        r.raise_for_status()
        resp = r.json()
    except Exception as e:
        LOG.warning("LLM request failed: %s", e)
        return None

    raw = (resp.get("choices") or [{}])[0].get("message", {}).get("content", "")
    content = raw.strip()
    if content.startswith("```"):
        content = "\n".join(content.split("\n")[1:]).rstrip("`").strip()

    try:
        parsed = json.loads(content)
    except Exception:
        LOG.warning("LLM non-JSON: %s", content[:200])
        return None

    action = str(parsed.get("action", "")).lower().strip()
    amount = parsed.get("amount")
    reason = str(parsed.get("reason", "llm"))
    if action == "bet":
        action = "raise"

    acts = legal_actions(snap)
    if action not in acts:
        LOG.warning("LLM illegal action '%s'", action)
        return None

    if action == "raise":
        if not isinstance(amount, int):
            return None
        amount = max(snap.min_raise, min(snap.max_raise, int(amount)))
    else:
        amount = None

    return Decision(action=action, amount=amount, source="llm", reason=reason)


# ---------------------------------------------------------------------------
# Bot polling loop (asyncio Task)
# ---------------------------------------------------------------------------

async def _bot_loop(game_id: str, bot_id: str, base_url: str, cfg: Config) -> None:
    """Async polling loop. Blocking HTTP calls run in a thread pool."""

    sess = _requests.Session()
    sess.headers.update({"Connection": "keep-alive"})

    # Join the game
    try:
        join_resp = await asyncio.to_thread(_post, sess, base_url, f"/games/{game_id}/join", {"player_name": cfg.player_name})
        token = join_resp.get("player_token", "")
        cfg.token = token
        me_resp = await asyncio.to_thread(_get, sess, base_url, "/me", token)
        entry = _bots.get(game_id, {}).get(bot_id)
        if entry:
            entry.token = token
            entry.player_id = me_resp.get("player_id", "")
    except Exception as e:
        LOG.error("Bot %s failed to join game %s: %s", bot_id, game_id, e)
        _bots.get(game_id, {}).pop(bot_id, None)
        sess.close()
        return

    LOG.info("Bot %s (%s) joined game %s", cfg.player_name, bot_id, game_id)

    try:
        while True:
            await asyncio.sleep(cfg.poll_interval)
            try:
                keep_running = await _tick(sess, base_url, game_id, bot_id, cfg)
                if not keep_running:
                    LOG.info("Bot %s stopping — game finished", bot_id)
                    break
            except asyncio.CancelledError:
                raise
            except Exception as e:
                LOG.warning("Bot %s tick error: %s", bot_id, e)
    except asyncio.CancelledError:
        pass
    finally:
        try:
            await asyncio.to_thread(_post, sess, base_url, f"/games/{game_id}/leave", token=cfg.token)
            LOG.info("Bot %s left game %s", bot_id, game_id)
        except Exception:
            pass
        sess.close()
        _bots.get(game_id, {}).pop(bot_id, None)


async def _tick(sess: _requests.Session, base_url: str, game_id: str, bot_id: str, cfg: Config) -> bool:
    """Run one polling cycle. Returns False when the bot should stop."""
    entry = _bots.get(game_id, {}).get(bot_id)
    if entry is None:
        return False

    token = cfg.token

    game = await asyncio.to_thread(_get, sess, base_url, f"/games/{game_id}")
    status = game.get("status", "")

    # Game over — signal the loop to stop
    if status == "finished":
        return False

    if status not in ("running", "paused"):
        return True

    # Fast-path: skip the 3 extra requests if it's clearly not our turn
    # and no rebuy check is needed.
    current_player_id = game.get("current_player_id")
    my_player_id = entry.player_id
    allow_rebuy = game.get("allow_rebuy", False)
    players_list = game.get("players", [])
    my_player_info = next((p for p in players_list if p.get("player_id") == my_player_id), None)
    my_chips = my_player_info.get("chips", 1) if my_player_info else 1
    my_status = my_player_info.get("status", "") if my_player_info else ""

    need_rebuy = allow_rebuy and my_chips == 0 and my_status == "eliminated"
    is_my_turn = (current_player_id == my_player_id)

    if not is_my_turn and not need_rebuy:
        return True

    # Fetch the full state only when we actually need to act
    hand_raw: dict = {}
    players_raw: dict = {}
    me_raw: dict = {}
    try:
        hand_raw = await asyncio.to_thread(_get, sess, base_url, f"/games/{game_id}/hand", token)
    except Exception:
        pass
    try:
        players_raw = await asyncio.to_thread(_get, sess, base_url, f"/games/{game_id}/players")
    except Exception:
        pass
    try:
        me_raw = await asyncio.to_thread(_get, sess, base_url, "/me", token)
    except Exception:
        pass

    snap = build_snapshot(game, hand_raw, players_raw, me_raw)

    # Auto-rebuy when eliminated
    if snap.allow_rebuy and snap.my_chips == 0 and snap.my_status == "eliminated":
        try:
            await asyncio.to_thread(_post, sess, base_url, f"/games/{game_id}/rebuy", token=token)
        except Exception:
            pass

    # Skip if hand not active
    hand_active = snap.street and snap.street not in ("", "showdown")
    if not hand_active:
        return True

    if not snap.is_my_turn:
        return True
    if snap.my_status in ("folded", "all_in", "sitting_out", "eliminated"):
        return True

    # Decide
    decision = None
    if cfg.llm_enabled:
        try:
            hist = await asyncio.to_thread(_get, sess, base_url, f"/games/{game_id}/history")
            snippet = history_snippet(hist)
            decision = await asyncio.to_thread(_llm_call_sync, cfg, snap, snippet)
            if decision:
                LOG.info("Bot %s LLM: %s(amount=%s) — %s", bot_id, decision.action, decision.amount, decision.reason)
        except Exception as e:
            LOG.warning("Bot %s LLM error: %s", bot_id, e)

    if decision is None:
        decision = heuristic_decision(snap, cfg.style)
        LOG.info("Bot %s heuristic[%s]: %s(amount=%s)", bot_id, cfg.style, decision.action, decision.amount)

    body: dict = {"action": decision.action}
    if decision.amount is not None:
        body["amount"] = decision.amount

    try:
        await asyncio.to_thread(_post, sess, base_url, f"/games/{game_id}/action", body, token)
    except Exception as e:
        LOG.error("Bot %s action failed: %s", bot_id, e)
        fallback = "check" if snap.can_check else "fold"
        if fallback != decision.action:
            try:
                await asyncio.to_thread(_post, sess, base_url, f"/games/{game_id}/action", {"action": fallback}, token)
            except Exception:
                pass

    return True


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.post("/{game_id}/bots", response_model=AddBotResponse, status_code=201)
async def add_bot(game_id: str, req: AddBotRequest) -> AddBotResponse:
    if req.style not in VALID_STYLES:
        raise HTTPException(400, detail=f"style must be one of: {', '.join(sorted(VALID_STYLES))}")

    import os
    base_url = os.getenv("POKER_BASE_URL", "http://localhost:8000").rstrip("/")

    cfg = Config()
    cfg.base_url = base_url
    cfg.game_id = game_id
    cfg.player_name = req.name
    cfg.style = req.style
    cfg.poll_interval = 2.0
    cfg.auto_rebuy = True
    cfg.llm_enabled = bool(req.llm_api_key and req.llm_endpoint)
    cfg.llm_api_key = req.llm_api_key
    cfg.llm_base_url = req.llm_endpoint.rstrip("/") if req.llm_endpoint else "https://api.openai.com/v1"
    cfg.llm_model = req.llm_model or "gpt-4o-mini"
    cfg.llm_timeout = 20.0
    cfg.llm_temperature = 0.1

    bot_id = str(uuid.uuid4())[:8]
    task = asyncio.create_task(_bot_loop(game_id, bot_id, base_url, cfg))

    entry = BotEntry(
        bot_id=bot_id,
        name=req.name,
        style=req.style,
        llm_enabled=cfg.llm_enabled,
        task=task,
    )

    if game_id not in _bots:
        _bots[game_id] = {}
    _bots[game_id][bot_id] = entry

    return AddBotResponse(bot_id=bot_id, name=req.name)


@router.get("/{game_id}/bots", response_model=BotListResponse)
async def list_bots(game_id: str) -> BotListResponse:
    entries = _bots.get(game_id, {})
    dead = [bid for bid, e in entries.items() if e.task.done()]
    for bid in dead:
        entries.pop(bid, None)
    return BotListResponse(bots=[
        BotInfo(
            bot_id=e.bot_id,
            name=e.name,
            style=e.style,
            llm_enabled=e.llm_enabled,
            player_id=e.player_id,
        )
        for e in entries.values()
    ])


@router.delete("/{game_id}/bots/{bot_id}", status_code=204)
async def kick_bot(game_id: str, bot_id: str):
    entry = _bots.get(game_id, {}).get(bot_id)
    if entry is None:
        raise HTTPException(404, detail="Bot not found")
    entry.task.cancel()
