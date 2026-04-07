# Texas Hold'em Poker Server

A REST API backend for managing Texas Hold'em poker game sessions. Built with FastAPI, SQLAlchemy async, and SQLite.

---

## Requirements

- Python 3.12+
- A `.venv` virtual environment (already created in this repo)

---

## Setup

```bash
# Install dependencies
.venv/bin/pip install -r requirements.txt

# Copy environment config (optional — defaults work out of the box)
cp .env.example .env

# Apply database migrations
.venv/bin/alembic upgrade head
```

---

## Running the Server

```bash
.venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

Interactive API docs available at: `http://localhost:8000/docs`

---

## Architecture

```
app/
├── main.py           FastAPI app, lifespan events, exception handlers
├── config.py         pydantic-settings config (reads from .env)
├── database.py       SQLAlchemy async engine + session factory
├── models/
│   ├── db.py         ORM models: Game, Player, Action, SidePot, HandResult
│   └── schemas.py    Pydantic v2 request/response schemas
├── core/
│   ├── poker.py      Pure card logic: deck, hand evaluation (no I/O)
│   ├── engine.py     Game state machine, all async game operations
│   └── auth.py       FastAPI auth dependencies (token → player)
└── api/
    ├── games.py      Game management routes
    └── actions.py    Player action routes
```

**Card encoding:** integers 0–51 where `rank = card // 4` (0=2…12=A) and `suit = card % 4` (0=c,1=d,2=h,3=s).

**Concurrency:** per-game `asyncio.Lock` serialises all writes. Safe for single-process uvicorn. Multi-worker deployments would need a distributed lock (e.g. Redis).

---

## API Reference

All action endpoints require `X-Player-Token: <uuid>` header.

### Game Management

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| POST | `/games` | none | Create a new game |
| POST | `/games/{id}/join` | none | Join a game |
| POST | `/games/{id}/start` | banker | Start the game |
| POST | `/games/{id}/next-hand` | banker | Deal next hand (after a hand ends) |
| GET | `/games/{id}` | none | Get public game state |
| GET | `/games/{id}/players` | none | List players and chip counts |
| GET | `/games/{id}/hand` | player | Get your private hole cards |

### Player Actions

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| POST | `/games/{id}/action` | player | Perform an action (check/call/raise/fold/all-in) |
| POST | `/games/{id}/rebuy` | player | Add chips (if game allows rebuys) |

---

## Walkthrough: Complete Game Session

### 1. Create a Game

```bash
curl -X POST http://localhost:8000/games \
  -H "Content-Type: application/json" \
  -d '{
    "banker_name": "Alice",
    "min_players": 2,
    "max_players": 6,
    "small_blind": 10,
    "big_blind": 20,
    "starting_chips": 1000,
    "allow_rebuy": true,
    "rebuy_amount": 500
  }'
```

Response:
```json
{
  "game_id": "abc123...",
  "banker_token": "tok-alice...",
  "banker_player_id": "pid-alice..."
}
```

### 2. Players Join

```bash
curl -X POST http://localhost:8000/games/abc123.../join \
  -H "Content-Type: application/json" \
  -d '{"player_name": "Bob"}'
```

Response:
```json
{
  "player_id": "pid-bob...",
  "player_token": "tok-bob...",
  "seat": 1,
  "starting_chips": 1000
}
```

### 3. Start the Game (banker only)

```bash
curl -X POST http://localhost:8000/games/abc123.../start \
  -H "X-Player-Token: tok-alice..."
```

Response includes full `game_state` with current street (`pre_flop`), blinds posted, and whose turn it is.

### 4. View Your Hand

```bash
curl http://localhost:8000/games/abc123.../hand \
  -H "X-Player-Token: tok-bob..."
```

Response:
```json
{
  "player_id": "pid-bob...",
  "hole_cards": [
    {"value": 48, "rank": "A", "suit": "c", "display": "Ac"},
    {"value": 35, "rank": "T", "suit": "s", "display": "Ts"}
  ],
  "community_cards": [],
  "best_hand": null,
  "hand_description": null
}
```

### 5. Perform Actions

```bash
# Call
curl -X POST http://localhost:8000/games/abc123.../action \
  -H "Content-Type: application/json" \
  -H "X-Player-Token: tok-bob..." \
  -d '{"action": "call"}'

# Raise by 100 (on top of any call amount)
curl -X POST http://localhost:8000/games/abc123.../action \
  -H "Content-Type: application/json" \
  -H "X-Player-Token: tok-alice..." \
  -d '{"action": "raise", "amount": 100}'

# Check
curl -X POST http://localhost:8000/games/abc123.../action \
  -H "Content-Type: application/json" \
  -H "X-Player-Token: tok-alice..." \
  -d '{"action": "check"}'

# Fold
curl -X POST http://localhost:8000/games/abc123.../action \
  -H "Content-Type: application/json" \
  -H "X-Player-Token: tok-alice..." \
  -d '{"action": "fold"}'

# All-in
curl -X POST http://localhost:8000/games/abc123.../action \
  -H "Content-Type: application/json" \
  -H "X-Player-Token: tok-alice..." \
  -d '{"action": "all_in"}'
```

Action response:
```json
{
  "success": true,
  "action": "call",
  "amount": 20,
  "new_chips": 980,
  "pot": 40,
  "next_player_id": "pid-alice...",
  "street": "flop",
  "message": "Action accepted"
}
```

### 6. Rebuy

```bash
curl -X POST http://localhost:8000/games/abc123.../rebuy \
  -H "X-Player-Token: tok-bob..."
```

### 7. Start Next Hand (after a hand ends)

```bash
curl -X POST http://localhost:8000/games/abc123.../next-hand \
  -H "X-Player-Token: tok-alice..."
```

---

## Game Rules Implemented

- **Streets:** pre_flop → flop → turn → river → showdown
- **Blinds:** small blind (SB) and big blind (BB) posted automatically; heads-up rule (dealer = SB, other = BB)
- **BB option:** Big blind gets the option to raise even if everyone called
- **Turn order:** clockwise from UTG pre-flop; from SB post-flop
- **Valid actions** are enforced per player state (can't check when there's a bet, can't raise less than previous raise size)
- **All-in fast path:** if all active players are all-in, remaining community cards are dealt immediately with no further betting
- **Side pots:** computed from sorted all-in amounts when multiple players go all-in
- **Showdown:** best 5-of-7 cards using all C(7,5)=21 combinations; ties split evenly (remainder to first player left of dealer)
- **Hand ranks (high to low):** Straight Flush, Four of a Kind, Full House, Flush, Straight, Three of a Kind, Two Pair, One Pair, High Card
- **Wheel straight:** A-2-3-4-5 correctly ranked below 6-high straight
- **Rebuy:** configurable amount; available to eliminated or between-hand players

---

## Game State Machine

```
WAITING → start() → RUNNING[pre_flop]
RUNNING[street] → betting closes, >1 player → RUNNING[next_street]
RUNNING[any] → only 1 player left → PAUSED (pot awarded)
RUNNING[river] → betting closes, >1 player → RUNNING[showdown] → PAUSED
PAUSED → next-hand, ≥min_players with chips → RUNNING[pre_flop]
PAUSED → next-hand, <min_players with chips → FINISHED
```

---

## Database

SQLite file at `./poker.db`. Schema managed by Alembic migrations in `alembic/versions/`.

Tables: `games`, `players`, `actions`, `side_pots`, `hand_results`

---

## Known Limitations

- **Single-process only:** The per-game concurrency lock is `asyncio.Lock` (in-memory). Multiple uvicorn workers would require a distributed lock.
- **No session persistence across restarts for locks:** Game state is in the database and survives restarts; the in-memory lock dict is recreated on restart (safe because SQLite serialises writes at the DB level too).

---

## WebSocket

Connect to receive real-time push events without polling:

```
ws://localhost:8000/games/{game_id}/ws?token=<player_token>
```

The `token` query parameter is optional. Authenticated connections receive all event types; unauthenticated connections receive public events only.

### Event types

**`game_state`** — broadcast to all connections after every state change (action, street advance, hand end):
```json
{"type": "game_state", "data": { ...same shape as GET /games/{id}... }}
```

**`action`** — broadcast after each player action:
```json
{
  "type": "action",
  "data": {
    "player_id": "pid-alice...",
    "action": "raise",
    "amount": 100,
    "pot": 240,
    "next_player_id": "pid-bob...",
    "street": "flop"
  }
}
```

**`hole_cards`** — sent only to the owning player after a new hand is dealt:
```json
{
  "type": "hole_cards",
  "data": {
    "player_id": "pid-bob...",
    "hole_cards": [{"value": 48, "rank": "A", "suit": "c", "display": "Ac"}, ...],
    "community_cards": [],
    "best_hand": null,
    "hand_description": null
  }
}
```

If an invalid token is supplied, the server closes the connection with WebSocket close code **4001**.
