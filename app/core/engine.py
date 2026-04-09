"""
Game state machine and all async game operations.

All public functions accept a SQLAlchemy AsyncSession and return updated ORM objects
or Pydantic response models. Callers must commit the session after calling these functions.

Concurrency: a per-game asyncio.Lock serialises all writes to a single game.
This is safe for single-process uvicorn. For multi-worker deployments a
distributed lock (e.g. Redis) would be required.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.poker import (
    card_to_dict,
    deal_cards,
    describe_hand,
    evaluate_best_hand,
    new_shuffled_deck,
)
from app.core.ws import manager
from app.database import AsyncSessionLocal
from app.models.db import (
    Action,
    ActionType,
    Game,
    GameStatus,
    HandResult,
    Player,
    PlayerStatus,
    SidePot,
    Street,
)
from app.models.schemas import (
    ActionResponse,
    CardModel,
    CreateGameRequest,
    CreateGameResponse,
    GameStateResponse,
    HandResponse,
    JoinGameRequest,
    JoinGameResponse,
    LeaveResponse,
    PlayerListResponse,
    PlayerPublicView,
    RebuyResponse,
    SessionRecoveryResponse,
    SidePotView,
    SitInResponse,
    SitOutResponse,
    StartGameResponse,
    TurnOptions,
)

# ---------------------------------------------------------------------------
# Per-game concurrency lock
# ---------------------------------------------------------------------------

_game_locks: dict[str, asyncio.Lock] = {}
_locks_mutex = asyncio.Lock()


async def _get_game_lock(game_id: str) -> asyncio.Lock:
    async with _locks_mutex:
        if game_id not in _game_locks:
            _game_locks[game_id] = asyncio.Lock()
        return _game_locks[game_id]


# ---------------------------------------------------------------------------
# Turn timeout
# ---------------------------------------------------------------------------

TURN_TIMEOUT_SECONDS = 60

_turn_timers: dict[str, asyncio.Task] = {}  # key: game_id


def _cancel_turn_timer(game_id: str) -> None:
    task = _turn_timers.pop(game_id, None)
    if task and not task.done():
        task.cancel()


def _schedule_turn_timer(game_id: str, player_id: str, expires_at: datetime) -> None:
    _cancel_turn_timer(game_id)
    task = asyncio.get_event_loop().create_task(
        _turn_timer_task(game_id, player_id, expires_at)
    )
    _turn_timers[game_id] = task


async def _turn_timer_task(game_id: str, player_id: str, expires_at: datetime) -> None:
    """Wait TURN_TIMEOUT_SECONDS then auto check-or-fold for the timed-out player."""
    try:
        await asyncio.sleep(TURN_TIMEOUT_SECONDS)
    except asyncio.CancelledError:
        return

    async with AsyncSessionLocal() as session:
        lock = await _get_game_lock(game_id)
        async with lock:
            result = await session.execute(
                select(Game).where(Game.id == game_id).options(selectinload(Game.players))
            )
            game = result.scalar_one_or_none()
            if game is None or game.status != GameStatus.RUNNING:
                return
            if game.current_player_id != player_id:
                # Turn already moved; nothing to do
                return

            player = next((p for p in game.players if p.id == player_id), None)
            if player is None or player.status != PlayerStatus.ACTIVE:
                return

            active = _active_players(game)
            max_bet = max((p.bet_this_street for p in active), default=0)
            to_call = max(0, max_bet - player.bet_this_street)
            auto_action = ActionType.CHECK if to_call == 0 else ActionType.FOLD

            # Apply the action inline (lock already held — cannot call process_action)
            seq = await _next_action_sequence(session, game)
            session.add(Action(
                game_id=game.id,
                player_id=player.id,
                hand_number=game.hand_number,
                street=game.current_street,
                action_type=auto_action,
                amount=None,
                sequence=seq,
            ))

            acted = game.players_acted
            if player.id not in acted:
                acted.append(player.id)
                game.players_acted = acted

            if auto_action == ActionType.FOLD:
                player.status = PlayerStatus.FOLDED
                non_folded = [
                    p for p in game.players
                    if p.status not in (PlayerStatus.FOLDED, PlayerStatus.ELIMINATED, PlayerStatus.SITTING_OUT)
                ]
                if len(non_folded) == 1:
                    winner = non_folded[0]
                    winner.chips += game.pot
                    game.pot = 0
                    game.status = GameStatus.PAUSED
                    game.current_player_id = None
                    _cancel_turn_timer(game_id)
                    await session.commit()
                    await manager.broadcast(game_id, {"type": "action", "data": {
                        "player_id": player_id, "action": "fold", "amount": None,
                        "pot": game.pot, "next_player_id": None, "street": game.current_street,
                    }})
                    game_state = await _build_game_state(session, game)
                    await manager.broadcast(game_id, {"type": "game_state", "data": game_state.model_dump()})
                    return
            # check or fold-but-game-continues
            if _is_betting_round_over(game):
                await _advance_street(session, game)
            else:
                next_player = _next_acting_player(game, player.id)
                if next_player:
                    game.current_player_id = next_player.id

            await session.commit()
            await session.refresh(game)

        # Outside lock: broadcast events and schedule next timer
        await manager.broadcast(game_id, {"type": "action", "data": {
            "player_id": player_id, "action": auto_action, "amount": None,
            "pot": game.pot, "next_player_id": game.current_player_id, "street": game.current_street,
        }})
        async with AsyncSessionLocal() as s2:
            result2 = await s2.execute(
                select(Game).where(Game.id == game_id).options(selectinload(Game.players))
            )
            g2 = result2.scalar_one_or_none()
            if g2:
                game_state = await _build_game_state(s2, g2)
                await manager.broadcast(game_id, {"type": "game_state", "data": game_state.model_dump()})
                if g2.status == GameStatus.RUNNING and g2.current_player_id:
                    next_expires = datetime.utcnow() + timedelta(seconds=TURN_TIMEOUT_SECONDS)
                    _schedule_turn_timer(game_id, g2.current_player_id, next_expires)
                    await manager.broadcast(game_id, {"type": "timer_sync", "data": {
                        "player_id": g2.current_player_id,
                        "expires_at": next_expires.isoformat() + "Z",
                    }})


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

async def _load_game(session: AsyncSession, game_id: str) -> Game:
    result = await session.execute(
        select(Game)
        .where(Game.id == game_id)
        .options(selectinload(Game.players))
    )
    game = result.scalar_one_or_none()
    if game is None:
        raise ValueError(f"Game {game_id} not found")
    return game


async def _load_side_pots(session: AsyncSession, game: Game) -> list[SidePot]:
    result = await session.execute(
        select(SidePot)
        .where(SidePot.game_id == game.id, SidePot.hand_number == game.hand_number)
        .order_by(SidePot.level)
    )
    return list(result.scalars().all())


async def _next_action_sequence(session: AsyncSession, game: Game) -> int:
    result = await session.execute(
        select(Action)
        .where(Action.game_id == game.id, Action.hand_number == game.hand_number)
        .order_by(Action.sequence.desc())
    )
    last = result.scalars().first()
    return (last.sequence + 1) if last else 0


# ---------------------------------------------------------------------------
# Helper: build response models
# ---------------------------------------------------------------------------

def _card_models(cards: list[int]) -> list[CardModel]:
    return [CardModel(**card_to_dict(c)) for c in cards]


async def _build_game_state(session: AsyncSession, game: Game) -> GameStateResponse:
    side_pots = await _load_side_pots(session, game)

    # Compute current_turn_options for the active player
    turn_options = None
    if game.status == GameStatus.RUNNING and game.current_player_id:
        current_player = next(
            (p for p in game.players if p.id == game.current_player_id), None
        )
        if current_player:
            active = _active_players(game)
            max_bet = max((p.bet_this_street for p in active), default=0)
            to_call = max(0, max_bet - current_player.bet_this_street)
            turn_options = TurnOptions(
                can_check=(to_call == 0),
                call_amount=to_call,
                min_raise=game.last_raise_size or game.big_blind,
                max_raise=current_player.chips,
                can_fold=True,
            )

    return GameStateResponse(
        game_id=game.id,
        status=game.status,
        street=game.current_street,
        hand_number=game.hand_number,
        pot=game.pot,
        community_cards=_card_models(game.community_cards),
        side_pots=[
            SidePotView(
                level=sp.level,
                amount=sp.amount,
                cap=sp.cap,
                eligible_player_ids=sp.eligible_player_ids,
            )
            for sp in side_pots
        ],
        players=[
            PlayerPublicView(
                player_id=p.id,
                name=p.name,
                seat=p.seat,
                chips=p.chips,
                role=p.role,
                status=p.status,
                bet_this_street=p.bet_this_street,
                is_current=(p.id == game.current_player_id),
            )
            for p in sorted(game.players, key=lambda x: x.seat)
        ],
        current_player_id=game.current_player_id,
        dealer_seat=game.dealer_seat,
        small_blind=game.small_blind,
        big_blind=game.big_blind,
        min_players=game.min_players,
        max_players=game.max_players,
        allow_rebuy=game.allow_rebuy,
        current_turn_options=turn_options,
    )


# ---------------------------------------------------------------------------
# Active player helpers
# ---------------------------------------------------------------------------

def _active_players(game: Game) -> list[Player]:
    """Players still in the hand (not folded, not eliminated, not sitting out)."""
    return sorted(
        [p for p in game.players if p.status in (PlayerStatus.ACTIVE, PlayerStatus.ALL_IN)],
        key=lambda p: p.seat,
    )


def _acting_players(game: Game) -> list[Player]:
    """Players who can still act (not folded, not all-in, not eliminated)."""
    return sorted(
        [p for p in game.players if p.status == PlayerStatus.ACTIVE],
        key=lambda p: p.seat,
    )


def _players_with_chips(game: Game) -> list[Player]:
    return [p for p in game.players if p.chips > 0]


# ---------------------------------------------------------------------------
# CREATE GAME
# ---------------------------------------------------------------------------

async def create_game(session: AsyncSession, req: CreateGameRequest) -> CreateGameResponse:
    game_id = str(uuid.uuid4())
    player_id = str(uuid.uuid4())
    token = str(uuid.uuid4())

    game = Game(
        id=game_id,
        status=GameStatus.WAITING,
        min_players=req.min_players,
        max_players=req.max_players,
        small_blind=req.small_blind,
        big_blind=req.big_blind,
        allow_rebuy=req.allow_rebuy,
        rebuy_amount=req.rebuy_amount,
        starting_chips=req.starting_chips,
        hand_number=0,
        last_raise_size=req.big_blind,
        pot=0,
    )
    game.community_cards = []
    game.deck_state = []
    game.players_acted = []
    session.add(game)
    await session.flush()  # get game.id into DB so player FK works

    banker = Player(
        id=player_id,
        game_id=game_id,
        token=token,
        name=req.banker_name,
        role="banker",
        seat=0,
        chips=req.starting_chips,
        status=PlayerStatus.ACTIVE,
        bet_this_street=0,
        total_bet_this_hand=0,
    )
    session.add(banker)
    await session.commit()

    return CreateGameResponse(
        game_id=game_id,
        banker_token=token,
        banker_player_id=player_id,
    )


# ---------------------------------------------------------------------------
# JOIN GAME
# ---------------------------------------------------------------------------

async def join_game(
    session: AsyncSession, game_id: str, req: JoinGameRequest
) -> JoinGameResponse:
    game = await _load_game(session, game_id)

    if game.status != GameStatus.WAITING:
        raise ValueError("Game has already started; new players cannot join")
    if len(game.players) >= game.max_players:
        raise ValueError("Game is full")

    seat = len(game.players)
    player_id = str(uuid.uuid4())
    token = str(uuid.uuid4())

    player = Player(
        id=player_id,
        game_id=game_id,
        token=token,
        name=req.player_name,
        role="player",
        seat=seat,
        chips=game.starting_chips,
        status=PlayerStatus.ACTIVE,
        bet_this_street=0,
        total_bet_this_hand=0,
    )
    session.add(player)
    await session.commit()

    await manager.broadcast(game_id, {
        "type": "player_joined",
        "data": PlayerPublicView(
            player_id=player_id,
            name=req.player_name,
            seat=seat,
            chips=game.starting_chips,
            role="player",
            status=PlayerStatus.ACTIVE,
            bet_this_street=0,
            is_current=False,
        ).model_dump(),
    })

    return JoinGameResponse(
        player_id=player_id,
        player_token=token,
        seat=seat,
        starting_chips=game.starting_chips,
    )


# ---------------------------------------------------------------------------
# START GAME
# ---------------------------------------------------------------------------

async def start_game(
    session: AsyncSession, game_id: str, banker_player: Player
) -> StartGameResponse:
    lock = await _get_game_lock(game_id)
    async with lock:
        game = await _load_game(session, game_id)

        if game.status != GameStatus.WAITING:
            raise ValueError("Game has already been started")
        if len(game.players) < game.min_players:
            raise ValueError(
                f"Need at least {game.min_players} players; currently {len(game.players)}"
            )

        game.status = GameStatus.RUNNING
        game.dealer_seat = 0
        await session.flush()

        await _deal_new_hand(session, game)
        await session.commit()

        await session.refresh(game)
        state = await _build_game_state(session, game)

        if game.status == GameStatus.RUNNING and game.current_player_id:
            expires_at = datetime.utcnow() + timedelta(seconds=TURN_TIMEOUT_SECONDS)
            _schedule_turn_timer(game_id, game.current_player_id, expires_at)
            await manager.broadcast(game_id, {"type": "timer_sync", "data": {
                "player_id": game.current_player_id,
                "expires_at": expires_at.isoformat() + "Z",
            }})

        return StartGameResponse(success=True, game_state=state)


# ---------------------------------------------------------------------------
# DEAL NEW HAND (internal)
# ---------------------------------------------------------------------------

async def _deal_new_hand(session: AsyncSession, game: Game) -> None:
    """
    Prepare and deal a new hand. Assumes game.status == RUNNING.
    Mutates game and player objects in place; caller must commit.
    """
    game.hand_number += 1
    game.pot = 0
    game.community_cards = []
    game.current_street = Street.PRE_FLOP
    game.last_raise_size = game.big_blind
    game.aggressor_player_id = None
    game.players_acted = []

    # Reset players
    eligible = [
        p for p in game.players
        if p.status != PlayerStatus.ELIMINATED and p.chips > 0
    ]
    for p in eligible:
        p.status = PlayerStatus.ACTIVE
        p.bet_this_street = 0
        p.total_bet_this_hand = 0
        p.hole_cards = None

    active = sorted(eligible, key=lambda p: p.seat)
    if len(active) < 2:
        game.status = GameStatus.FINISHED
        return

    # Rotate dealer button (skip eliminated/no-chip players)
    active_seats = [p.seat for p in active]
    current_dealer_seat = game.dealer_seat or 0
    # Find next active seat after current dealer
    seats_after = [s for s in active_seats if s > current_dealer_seat]
    game.dealer_seat = seats_after[0] if seats_after else active_seats[0]

    # Shuffle and deal hole cards
    deck = new_shuffled_deck()
    for p in active:
        dealt, deck = deal_cards(deck, 2)
        p.hole_cards = dealt
    game.deck_state = deck

    # Post blinds
    seq = 0
    heads_up = len(active) == 2

    if heads_up:
        # Dealer = SB, other = BB; dealer acts first pre-flop
        sb_player = next(p for p in active if p.seat == game.dealer_seat)
        bb_player = next(p for p in active if p.seat != game.dealer_seat)
    else:
        dealer_idx = next(i for i, p in enumerate(active) if p.seat == game.dealer_seat)
        sb_player = active[(dealer_idx + 1) % len(active)]
        bb_player = active[(dealer_idx + 2) % len(active)]

    # Small blind
    sb_amount = min(game.small_blind, sb_player.chips)
    sb_player.chips -= sb_amount
    sb_player.bet_this_street = sb_amount
    sb_player.total_bet_this_hand = sb_amount
    if sb_player.chips == 0:
        sb_player.status = PlayerStatus.ALL_IN
    game.pot += sb_amount
    session.add(Action(
        game_id=game.id,
        player_id=sb_player.id,
        hand_number=game.hand_number,
        street=Street.PRE_FLOP,
        action_type=ActionType.BLIND,
        amount=sb_amount,
        sequence=seq,
    ))
    seq += 1

    # Big blind
    bb_amount = min(game.big_blind, bb_player.chips)
    bb_player.chips -= bb_amount
    bb_player.bet_this_street = bb_amount
    bb_player.total_bet_this_hand = bb_amount
    if bb_player.chips == 0:
        bb_player.status = PlayerStatus.ALL_IN
    game.pot += bb_amount
    session.add(Action(
        game_id=game.id,
        player_id=bb_player.id,
        hand_number=game.hand_number,
        street=Street.PRE_FLOP,
        action_type=ActionType.BLIND,
        amount=bb_amount,
        sequence=seq,
    ))

    # BB is the aggressor (gets the option)
    game.aggressor_player_id = bb_player.id

    # First to act pre-flop: UTG (left of BB), or dealer in heads-up
    if heads_up:
        game.current_player_id = sb_player.id
    else:
        dealer_idx = next(i for i, p in enumerate(active) if p.seat == game.dealer_seat)
        utg = active[(dealer_idx + 3) % len(active)]
        game.current_player_id = utg.id

    # Rebuild side pots (clean state for new hand)
    await _delete_current_side_pots(session, game)


async def _delete_current_side_pots(session: AsyncSession, game: Game) -> None:
    existing = await _load_side_pots(session, game)
    for sp in existing:
        await session.delete(sp)


# ---------------------------------------------------------------------------
# SIDE POT CONSTRUCTION
# ---------------------------------------------------------------------------

async def _rebuild_side_pots(session: AsyncSession, game: Game) -> None:
    """
    Recompute side pots from scratch based on total_bet_this_hand values.
    All-in players create pot caps. Written to side_pots table.
    """
    await _delete_current_side_pots(session, game)

    active = _active_players(game)  # not folded/eliminated
    if not active:
        return

    all_in_players = [p for p in active if p.status == PlayerStatus.ALL_IN]
    all_in_amounts = sorted(set(p.total_bet_this_hand for p in all_in_players))

    # Add a final uncapped level for non-all-in contributions
    caps = all_in_amounts + [None]

    prev_cap = 0
    eligible = list(active)

    for level, cap in enumerate(caps):
        if cap is not None:
            per_player_contrib = [
                min(cap, p.total_bet_this_hand) - min(prev_cap, p.total_bet_this_hand)
                for p in active  # include ALL active (even folded contributions up to cap)
            ]
            # Also include folded players' contributions up to this cap
            folded = [
                p for p in game.players
                if p.status == PlayerStatus.FOLDED and p.total_bet_this_hand > 0
            ]
            for fp in folded:
                contrib = min(cap, fp.total_bet_this_hand) - min(prev_cap, fp.total_bet_this_hand)
                per_player_contrib.append(contrib)

            pot_amount = sum(per_player_contrib)
            if pot_amount <= 0:
                prev_cap = cap
                continue

            sp = SidePot(
                game_id=game.id,
                hand_number=game.hand_number,
                level=level,
                amount=pot_amount,
                cap=cap,
            )
            sp.eligible_player_ids = [p.id for p in eligible if p.id not in [
                ai.id for ai in all_in_players if ai.total_bet_this_hand <= prev_cap
            ]]
            session.add(sp)
            # Remove players who are all-in at exactly this cap from higher pots
            eligible = [p for p in eligible if p.total_bet_this_hand > cap]
            prev_cap = cap
        else:
            # Uncapped pot: remaining eligible players
            remaining_contribs = [
                p.total_bet_this_hand - prev_cap
                for p in active
                if p.total_bet_this_hand > prev_cap
            ]
            folded = [
                p for p in game.players
                if p.status == PlayerStatus.FOLDED and p.total_bet_this_hand > prev_cap
            ]
            for fp in folded:
                remaining_contribs.append(fp.total_bet_this_hand - prev_cap)

            pot_amount = sum(remaining_contribs)
            if pot_amount <= 0:
                break

            sp = SidePot(
                game_id=game.id,
                hand_number=game.hand_number,
                level=level,
                amount=pot_amount,
                cap=None,
            )
            sp.eligible_player_ids = [p.id for p in eligible]
            session.add(sp)

    await session.flush()


# ---------------------------------------------------------------------------
# BETTING ROUND HELPERS
# ---------------------------------------------------------------------------

def _is_betting_round_over(game: Game) -> bool:
    """
    Returns True when:
    1. All acting (non-folded, non-all-in) players have bet_this_street == max_bet, AND
    2. All acting players appear in game.players_acted (acted at least once this street).
    """
    actors = _acting_players(game)
    if not actors:
        return True

    max_bet = max(p.bet_this_street for p in _active_players(game)) if _active_players(game) else 0
    acted = set(game.players_acted)

    for p in actors:
        if p.bet_this_street < max_bet:
            return False
        if p.id not in acted:
            return False

    return True


def _next_acting_player(game: Game, after_player_id: str) -> Player | None:
    """
    Returns the next player who must act clockwise after after_player_id.
    Returns None only if there are no acting players left.
    Round-over check is handled separately by _is_betting_round_over.
    """
    actors = _acting_players(game)
    if not actors:
        return None

    all_sorted = sorted(game.players, key=lambda p: p.seat)
    after_seat = next((p.seat for p in all_sorted if p.id == after_player_id), -1)

    actors_after = [p for p in actors if p.seat > after_seat]
    actors_wrap = [p for p in actors if p.seat <= after_seat]
    ordered = actors_after + actors_wrap

    return ordered[0] if ordered else None


def _would_close_round(game: Game, candidate: Player) -> bool:
    """Kept for compatibility; logic moved to _is_betting_round_over."""
    return False


# ---------------------------------------------------------------------------
# ADVANCE STREET
# ---------------------------------------------------------------------------

async def _advance_street(session: AsyncSession, game: Game) -> None:
    """
    Move to the next street, deal community cards, reset bets.
    If all active players are all-in, fast-forward to showdown.
    """
    # Reset per-street state
    for p in game.players:
        p.bet_this_street = 0
    game.last_raise_size = game.big_blind
    game.aggressor_player_id = None
    game.players_acted = []

    street_order = [Street.PRE_FLOP, Street.FLOP, Street.TURN, Street.RIVER, Street.SHOWDOWN]
    current_idx = street_order.index(game.current_street)
    next_street = street_order[current_idx + 1]
    game.current_street = next_street

    deck = game.deck_state
    community = game.community_cards

    if next_street == Street.FLOP:
        dealt, deck = deal_cards(deck, 3)
        community = community + dealt
    elif next_street in (Street.TURN, Street.RIVER):
        dealt, deck = deal_cards(deck, 1)
        community = community + dealt

    game.deck_state = deck
    game.community_cards = community

    if next_street == Street.SHOWDOWN:
        await _perform_showdown(session, game)
        return

    # Determine first to act (first active left of dealer)
    active = _acting_players(game)
    if not active:
        # All are all-in or folded; fast-forward
        await _advance_street(session, game)
        return

    dealer_seat = game.dealer_seat or 0
    after_dealer = [p for p in active if p.seat > dealer_seat]
    first_actor = after_dealer[0] if after_dealer else active[0]
    game.current_player_id = first_actor.id

    # If everyone is all-in, fast-forward
    if not _acting_players(game):
        await _advance_street(session, game)


# ---------------------------------------------------------------------------
# SHOWDOWN
# ---------------------------------------------------------------------------

async def _perform_showdown(session: AsyncSession, game: Game) -> None:
    """Evaluate hands, distribute pots, record results, transition to PAUSED."""
    side_pots = await _load_side_pots(session, game)
    community = game.community_cards

    # If no side pots were created (no all-ins), create a single main pot
    if not side_pots:
        active = _active_players(game)
        sp = SidePot(
            game_id=game.id,
            hand_number=game.hand_number,
            level=0,
            amount=game.pot,
            cap=None,
        )
        sp.eligible_player_ids = [p.id for p in active]
        session.add(sp)
        await session.flush()
        side_pots = [sp]

    dealer_seat = game.dealer_seat or 0
    active_sorted = sorted(_active_players(game), key=lambda p: p.seat)

    # Evaluate hands for all eligible players
    hand_scores: dict[str, tuple] = {}
    best_fives: dict[str, list[int]] = {}
    for p in _active_players(game):
        if len(p.hole_cards) == 2 and len(community) >= 3:
            score, best_five = evaluate_best_hand(p.hole_cards + community)
        elif len(p.hole_cards) == 2:
            score, best_five = evaluate_best_hand(p.hole_cards + community) if len(p.hole_cards + community) >= 5 else ((0, tuple()), p.hole_cards)
        else:
            score, best_five = (0, ()), []
        hand_scores[p.id] = score
        best_fives[p.id] = best_five

    pot_won: dict[str, int] = {p.id: 0 for p in game.players}

    for sp in side_pots:
        eligible_ids = sp.eligible_player_ids
        contenders = [p for p in _active_players(game) if p.id in eligible_ids]
        if not contenders:
            continue

        if len(contenders) == 1:
            contenders[0].chips += sp.amount
            pot_won[contenders[0].id] += sp.amount
            continue

        best_score = max(hand_scores[p.id] for p in contenders)
        winners = [p for p in contenders if hand_scores[p.id] == best_score]
        split = sp.amount // len(winners)
        remainder = sp.amount % len(winners)

        for w in winners:
            w.chips += split
            pot_won[w.id] += split

        # Remainder to first winner left of dealer
        if remainder > 0:
            seats_after = [w for w in winners if w.seat > dealer_seat]
            leftmost = seats_after[0] if seats_after else winners[0]
            leftmost.chips += remainder
            pot_won[leftmost.id] += remainder

    # Record hand results
    for p in _active_players(game):
        score = hand_scores.get(p.id, (0, ()))
        hr = HandResult(
            game_id=game.id,
            hand_number=game.hand_number,
            player_id=p.id,
            hand_rank=score[0] if score else None,
            hand_description=describe_hand(score[0], score[1]) if score and len(community) >= 3 else None,
            pot_won=pot_won.get(p.id, 0),
        )
        hr.hole_cards = p.hole_cards
        hr.best_hand = best_fives.get(p.id)
        session.add(hr)

    # Mark eliminated players
    for p in game.players:
        if p.chips == 0 and p.status != PlayerStatus.FOLDED:
            p.status = PlayerStatus.ELIMINATED

    game.pot = 0
    game.current_player_id = None
    game.status = GameStatus.PAUSED

    await session.flush()

    # Broadcast showdown reveal so clients can animate hole cards
    reveal_players = [p for p in game.players if p.id in hand_scores]
    await manager.broadcast(game.id, {
        "type": "showdown_reveal",
        "data": [
            {
                "player_id": p.id,
                "hole_cards": _card_models(p.hole_cards),
            }
            for p in reveal_players
        ],
    })


# ---------------------------------------------------------------------------
# PROCESS ACTION
# ---------------------------------------------------------------------------

async def process_action(
    session: AsyncSession,
    game_id: str,
    acting_player: Player,
    action: str,
    amount: int | None,
) -> ActionResponse:
    lock = await _get_game_lock(game_id)
    async with lock:
        game = await _load_game(session, game_id)

        if game.status != GameStatus.RUNNING:
            raise ValueError("Game is not currently running")
        if game.current_player_id != acting_player.id:
            raise ValueError("It is not your turn")

        action_type = ActionType(action)
        active = _active_players(game)
        max_bet = max(p.bet_this_street for p in active) if active else 0
        to_call = max_bet - acting_player.bet_this_street

        # Validate and apply action
        chip_delta = 0
        actual_amount: int | None = None

        if action_type == ActionType.FOLD:
            acting_player.status = PlayerStatus.FOLDED

        elif action_type == ActionType.CHECK:
            if to_call > 0:
                raise ValueError(f"Cannot check; there is a bet of {to_call} to call")

        elif action_type == ActionType.CALL:
            if to_call <= 0:
                raise ValueError("Nothing to call; use check instead")
            call_amount = min(to_call, acting_player.chips)
            acting_player.chips -= call_amount
            acting_player.bet_this_street += call_amount
            acting_player.total_bet_this_hand += call_amount
            game.pot += call_amount
            chip_delta = -call_amount
            actual_amount = call_amount
            if acting_player.chips == 0:
                acting_player.status = PlayerStatus.ALL_IN

        elif action_type == ActionType.RAISE:
            if amount is None:
                raise ValueError("Amount required for raise")
            min_raise = game.last_raise_size
            total_raise = to_call + amount  # amount = size of raise on top of call
            if amount < min_raise and total_raise < acting_player.chips:
                raise ValueError(f"Raise must be at least {min_raise} (the previous raise size)")
            if total_raise > acting_player.chips:
                raise ValueError("Raise exceeds your chip stack; use all-in instead")
            acting_player.chips -= total_raise
            acting_player.bet_this_street += total_raise
            acting_player.total_bet_this_hand += total_raise
            game.pot += total_raise
            chip_delta = -total_raise
            actual_amount = total_raise
            game.last_raise_size = amount
            game.aggressor_player_id = acting_player.id
            if acting_player.chips == 0:
                acting_player.status = PlayerStatus.ALL_IN

        elif action_type == ActionType.ALL_IN:
            all_in_amount = acting_player.chips
            acting_player.chips = 0
            acting_player.bet_this_street += all_in_amount
            acting_player.total_bet_this_hand += all_in_amount
            game.pot += all_in_amount
            chip_delta = -all_in_amount
            actual_amount = all_in_amount
            acting_player.status = PlayerStatus.ALL_IN
            # If this all-in is a raise, update aggressor
            new_bet = acting_player.bet_this_street
            if new_bet > max_bet:
                game.last_raise_size = new_bet - max_bet
                game.aggressor_player_id = acting_player.id

        else:
            raise ValueError(f"Invalid action: {action_type}")

        # Record the action
        seq = await _next_action_sequence(session, game)
        session.add(Action(
            game_id=game.id,
            player_id=acting_player.id,
            hand_number=game.hand_number,
            street=game.current_street,
            action_type=action_type,
            amount=actual_amount,
            sequence=seq,
        ))

        # Track that this player has acted this street
        acted = game.players_acted
        if acting_player.id not in acted:
            acted.append(acting_player.id)
            game.players_acted = acted

        # Rebuild side pots if an all-in occurred
        if action_type == ActionType.ALL_IN or (action_type == ActionType.CALL and acting_player.status == PlayerStatus.ALL_IN):
            await _rebuild_side_pots(session, game)

        # Determine next state
        remaining_active = _active_players(game)
        only_one_left = len([p for p in remaining_active if p.status != PlayerStatus.ALL_IN]) <= 0 and len(remaining_active) <= 1 or len([p for p in game.players if p.status not in (PlayerStatus.FOLDED, PlayerStatus.ELIMINATED, PlayerStatus.SITTING_OUT)]) == 1

        # Check if only one non-folded player remains
        non_folded = [p for p in game.players if p.status not in (PlayerStatus.FOLDED, PlayerStatus.ELIMINATED, PlayerStatus.SITTING_OUT)]
        if len(non_folded) == 1:
            # Award pot to last player
            winner = non_folded[0]
            winner.chips += game.pot
            game.pot = 0

            # Record hand result
            hr = HandResult(
                game_id=game.id,
                hand_number=game.hand_number,
                player_id=winner.id,
                hand_rank=None,
                hand_description="Won (all others folded)",
                pot_won=winner.chips - (winner.chips - game.pot) if game.pot > 0 else 0,
            )
            hr.hole_cards = winner.hole_cards
            hr.best_hand = None
            session.add(hr)

            game.status = GameStatus.PAUSED
            game.current_player_id = None
            _cancel_turn_timer(game_id)
            await session.commit()

            return ActionResponse(
                success=True,
                action=action_type,
                amount=actual_amount,
                new_chips=acting_player.chips,
                pot=game.pot,
                next_player_id=None,
                street=game.current_street,
                message=f"{winner.name} wins the pot",
            )

        # Advance turn or street
        next_player = _next_acting_player(game, acting_player.id)
        if next_player is None or _is_betting_round_over_after_action(game, acting_player):
            # Street is over
            await _advance_street(session, game)
        else:
            game.current_player_id = next_player.id

        await session.commit()
        await session.refresh(game)

        # Schedule timer for whoever's turn it is now
        if game.status == GameStatus.RUNNING and game.current_player_id:
            expires_at = datetime.utcnow() + timedelta(seconds=TURN_TIMEOUT_SECONDS)
            _schedule_turn_timer(game_id, game.current_player_id, expires_at)
            await manager.broadcast(game_id, {"type": "timer_sync", "data": {
                "player_id": game.current_player_id,
                "expires_at": expires_at.isoformat() + "Z",
            }})
        elif game.status in (GameStatus.PAUSED, GameStatus.FINISHED):
            _cancel_turn_timer(game_id)

        return ActionResponse(
            success=True,
            action=action_type,
            amount=actual_amount,
            new_chips=acting_player.chips,
            pot=game.pot,
            next_player_id=game.current_player_id,
            street=game.current_street,
            message="Action accepted",
        )


def _is_betting_round_over_after_action(game: Game, last_actor: Player) -> bool:
    """Delegates to _is_betting_round_over which uses players_acted for correctness."""
    return _is_betting_round_over(game)


# ---------------------------------------------------------------------------
# REBUY
# ---------------------------------------------------------------------------

async def process_rebuy(
    session: AsyncSession, game_id: str, acting_player: Player
) -> RebuyResponse:
    lock = await _get_game_lock(game_id)
    async with lock:
        game = await _load_game(session, game_id)

        if not game.allow_rebuy:
            raise ValueError("Rebuys are not allowed in this game")
        if game.rebuy_amount is None:
            raise ValueError("No rebuy amount configured")
        if acting_player.status not in (PlayerStatus.ELIMINATED, PlayerStatus.ACTIVE, PlayerStatus.SITTING_OUT):
            raise ValueError("You can only rebuy when you are eliminated or between hands")

        acting_player.chips += game.rebuy_amount
        if acting_player.status == PlayerStatus.ELIMINATED:
            acting_player.status = PlayerStatus.ACTIVE

        seq = await _next_action_sequence(session, game)
        session.add(Action(
            game_id=game.id,
            player_id=acting_player.id,
            hand_number=game.hand_number,
            street=game.current_street or Street.PRE_FLOP,
            action_type=ActionType.REBUY,
            amount=game.rebuy_amount,
            sequence=seq,
        ))

        await session.commit()

        return RebuyResponse(
            success=True,
            new_chips=acting_player.chips,
            amount_added=game.rebuy_amount,
        )


# ---------------------------------------------------------------------------
# READ-ONLY QUERIES
# ---------------------------------------------------------------------------

async def get_game_state(session: AsyncSession, game_id: str) -> GameStateResponse:
    game = await _load_game(session, game_id)
    return await _build_game_state(session, game)


async def get_player_hand(
    session: AsyncSession, game_id: str, acting_player: Player
) -> HandResponse:
    game = await _load_game(session, game_id)
    community = game.community_cards

    # At showdown, look up best hand from hand_results
    best_hand_cards = None
    hand_description = None
    if game.current_street == Street.SHOWDOWN:
        result = await session.execute(
            select(HandResult)
            .where(
                HandResult.game_id == game_id,
                HandResult.hand_number == game.hand_number,
                HandResult.player_id == acting_player.id,
            )
        )
        hr = result.scalar_one_or_none()
        if hr and hr.best_hand:
            best_hand_cards = _card_models(hr.best_hand)
            hand_description = hr.hand_description

    return HandResponse(
        player_id=acting_player.id,
        hole_cards=_card_models(acting_player.hole_cards),
        community_cards=_card_models(community),
        best_hand=best_hand_cards,
        hand_description=hand_description,
    )


async def get_player_hand_by_id(
    session: AsyncSession, game_id: str, player_id: str
) -> HandResponse:
    result = await session.execute(
        select(Player).where(Player.id == player_id, Player.game_id == game_id)
    )
    player = result.scalar_one_or_none()
    if player is None:
        raise ValueError(f"Player {player_id} not found in game {game_id}")
    return await get_player_hand(session, game_id, player)


async def get_players(session: AsyncSession, game_id: str) -> PlayerListResponse:
    game = await _load_game(session, game_id)
    return PlayerListResponse(
        players=[
            PlayerPublicView(
                player_id=p.id,
                name=p.name,
                seat=p.seat,
                chips=p.chips,
                role=p.role,
                status=p.status,
                bet_this_street=p.bet_this_street,
                is_current=(p.id == game.current_player_id),
            )
            for p in sorted(game.players, key=lambda x: x.seat)
        ],
        total_chips_in_play=sum(p.chips for p in game.players),
    )


# ---------------------------------------------------------------------------
# START NEXT HAND (called by client between hands)
# ---------------------------------------------------------------------------

async def start_next_hand(session: AsyncSession, game_id: str, banker_player: Player) -> StartGameResponse:
    lock = await _get_game_lock(game_id)
    async with lock:
        game = await _load_game(session, game_id)

        if game.status != GameStatus.PAUSED:
            raise ValueError("Game is not in a paused state between hands")

        eligible = _players_with_chips(game)
        if len(eligible) < game.min_players:
            game.status = GameStatus.FINISHED
            await session.commit()
            state = await _build_game_state(session, game)
            return StartGameResponse(success=True, game_state=state)

        game.status = GameStatus.RUNNING
        await _deal_new_hand(session, game)
        await session.commit()
        await session.refresh(game)

        state = await _build_game_state(session, game)

        if game.status == GameStatus.RUNNING and game.current_player_id:
            expires_at = datetime.utcnow() + timedelta(seconds=TURN_TIMEOUT_SECONDS)
            _schedule_turn_timer(game_id, game.current_player_id, expires_at)
            await manager.broadcast(game_id, {"type": "timer_sync", "data": {
                "player_id": game.current_player_id,
                "expires_at": expires_at.isoformat() + "Z",
            }})

        return StartGameResponse(success=True, game_state=state)


# ---------------------------------------------------------------------------
# SESSION RECOVERY
# ---------------------------------------------------------------------------

async def get_player_by_token(
    session: AsyncSession, token: str
) -> Player | None:
    result = await session.execute(
        select(Player).where(Player.token == token)
    )
    return result.scalar_one_or_none()


# ---------------------------------------------------------------------------
# LOBBY
# ---------------------------------------------------------------------------

async def list_games(session: AsyncSession) -> list[dict]:
    """Return games that are WAITING or RUNNING with open seats."""
    result = await session.execute(
        select(Game).options(selectinload(Game.players))
        .where(Game.status.in_([GameStatus.WAITING, GameStatus.RUNNING]))
    )
    games = result.scalars().all()
    out = []
    for game in games:
        non_eliminated = [
            p for p in game.players
            if p.status != PlayerStatus.ELIMINATED
        ]
        player_count = len(non_eliminated)
        # Include WAITING games always; RUNNING games only if seats remain
        if game.status == GameStatus.RUNNING and player_count >= game.max_players:
            continue
        out.append({
            "game_id": game.id,
            "status": game.status,
            "player_count": player_count,
            "max_players": game.max_players,
            "small_blind": game.small_blind,
            "big_blind": game.big_blind,
            "created_at": game.created_at,
        })
    return out


# ---------------------------------------------------------------------------
# SIT OUT / SIT IN
# ---------------------------------------------------------------------------

async def sit_out(
    session: AsyncSession, game_id: str, acting_player: Player
) -> SitOutResponse:
    game = await _load_game(session, game_id)
    if game.status == GameStatus.RUNNING:
        raise ValueError("Cannot sit out during a running hand; wait until the hand ends")
    if acting_player.status not in (PlayerStatus.ACTIVE,):
        raise ValueError("Can only sit out when active")
    acting_player.status = PlayerStatus.SITTING_OUT
    await session.commit()
    game_state = await _build_game_state(session, game)
    await manager.broadcast(game_id, {"type": "game_state", "data": game_state.model_dump()})
    return SitOutResponse(success=True)


async def sit_in(
    session: AsyncSession, game_id: str, acting_player: Player
) -> SitInResponse:
    game = await _load_game(session, game_id)
    if acting_player.status != PlayerStatus.SITTING_OUT:
        raise ValueError("Player is not sitting out")
    acting_player.status = PlayerStatus.ACTIVE
    await session.commit()
    game_state = await _build_game_state(session, game)
    await manager.broadcast(game_id, {"type": "game_state", "data": game_state.model_dump()})
    return SitInResponse(success=True)


# ---------------------------------------------------------------------------
# LEAVE GAME
# ---------------------------------------------------------------------------

async def leave_game(
    session: AsyncSession, game_id: str, acting_player: Player
) -> LeaveResponse:
    lock = await _get_game_lock(game_id)
    async with lock:
        game = await _load_game(session, game_id)

        if game.status == GameStatus.WAITING:
            # Just remove the player from the lobby
            await session.delete(acting_player)
            await session.commit()
            await manager.broadcast(game_id, {
                "type": "player_left",
                "data": {"player_id": acting_player.id, "name": acting_player.name, "seat": acting_player.seat},
            })
            return LeaveResponse(success=True, message="Left the game")

        # In a running or paused game: fold if needed, then eliminate
        if acting_player.status == PlayerStatus.ACTIVE and game.status == GameStatus.RUNNING:
            # If it's the leaving player's turn, fold inline (cannot call process_action — same lock)
            if game.current_player_id == acting_player.id:
                acting_player.status = PlayerStatus.FOLDED
                acted = game.players_acted
                if acting_player.id not in acted:
                    acted.append(acting_player.id)
                    game.players_acted = acted

                # Check if only one non-folded player remains
                non_folded = [
                    p for p in game.players
                    if p.status not in (PlayerStatus.FOLDED, PlayerStatus.ELIMINATED, PlayerStatus.SITTING_OUT)
                ]
                if len(non_folded) == 1:
                    winner = non_folded[0]
                    winner.chips += game.pot
                    game.pot = 0
                    game.status = GameStatus.PAUSED
                    game.current_player_id = None
                elif _is_betting_round_over(game):
                    await _advance_street(session, game)
                else:
                    next_player = _next_acting_player(game, acting_player.id)
                    if next_player:
                        game.current_player_id = next_player.id
            else:
                # Not their turn — fold them out of the hand silently
                acting_player.status = PlayerStatus.FOLDED

        # Eliminate from future hands
        acting_player.status = PlayerStatus.ELIMINATED
        acting_player.chips = 0

        await session.commit()

        await manager.broadcast(game_id, {
            "type": "player_left",
            "data": {"player_id": acting_player.id, "name": acting_player.name, "seat": acting_player.seat},
        })
        game_state = await _build_game_state(session, game)
        await manager.broadcast(game_id, {"type": "game_state", "data": game_state.model_dump()})

        return LeaveResponse(success=True, message="Left the game")


# ---------------------------------------------------------------------------
# HAND HISTORY
# ---------------------------------------------------------------------------

async def get_hand_history(session: AsyncSession, game_id: str) -> list[dict]:
    result = await session.execute(
        select(Action, Player.name.label("player_name"))
        .join(Player, Player.id == Action.player_id)
        .where(Action.game_id == game_id)
        .order_by(Action.hand_number.desc(), Action.sequence.asc())
        .limit(100)
    )
    rows = result.all()
    return [
        {
            "hand_number": row.Action.hand_number,
            "street": row.Action.street,
            "player_id": row.Action.player_id,
            "player_name": row.player_name,
            "action_type": row.Action.action_type,
            "amount": row.Action.amount,
            "sequence": row.Action.sequence,
            "created_at": row.Action.created_at,
        }
        for row in rows
    ]
