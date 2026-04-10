#!/usr/bin/env python3
"""
Texas Hold'em Autonomous Bot
============================
Single-file bot that connects to the poker server REST API and plays hands
automatically using deterministic heuristics with optional LLM assistance.

Auth note
---------
The server uses the header  X-Player-Token: <token>  for all authenticated
endpoints (get_current_player / require_banker).  Set POKER_TOKEN to the
player token you received when you joined (or the banker token if you are
the banker).  The bot also accepts POKER_BANKER_TOKEN separately so it can
call banker-only endpoints (start, next-hand) while playing as a regular
player — if unset, POKER_TOKEN is tried for both roles.

Schema assumptions (marked [SCHEMA])
-------------------------------------
All assumptions are derived from app/models/schemas.py and app/models/db.py.
If the server is ever updated, search for [SCHEMA] to find every assumption.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import random
import signal
import sys
import time
from dataclasses import dataclass, field
from itertools import combinations
from typing import Any

try:
    import requests
except ImportError:
    sys.exit("'requests' is required. Install with:  pip install requests")

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

LOG = logging.getLogger("poker_bot")
_handler = logging.StreamHandler(sys.stdout)
_handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", "%H:%M:%S"))
LOG.addHandler(_handler)
LOG.setLevel(logging.DEBUG)


def _verbose() -> bool:
    return os.getenv("POKER_VERBOSE", "0") == "1"


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

@dataclass
class Config:
    base_url: str = ""
    token: str = ""           # player token  → X-Player-Token
    banker_token: str = ""    # banker token  → X-Player-Token for banker calls
    game_id: str = ""
    player_name: str = "PokerBot"
    poll_interval: float = 2.0
    auto_start: bool = False
    auto_next_hand: bool = False
    auto_rebuy: bool = False
    rebuy_threshold: int = 200
    rebuy_amount: int | None = None

    # LLM
    llm_enabled: bool = False
    llm_api_key: str = ""
    llm_base_url: str = "https://api.openai.com/v1"
    llm_model: str = "gpt-4o-mini"
    llm_timeout: float = 20.0
    llm_temperature: float = 0.1

    @classmethod
    def from_env(cls) -> "Config":
        c = cls()
        c.base_url = os.getenv("POKER_BASE_URL", "http://localhost:8000").rstrip("/")
        c.token = os.getenv("POKER_TOKEN", "")
        c.banker_token = os.getenv("POKER_BANKER_TOKEN", "") or c.token
        c.game_id = os.getenv("POKER_GAME_ID", "")
        c.player_name = os.getenv("POKER_PLAYER_NAME", "PokerBot")
        c.poll_interval = float(os.getenv("POKER_POLL_INTERVAL", "2"))
        c.auto_start = os.getenv("POKER_AUTO_START", "0") == "1"
        c.auto_next_hand = os.getenv("POKER_AUTO_NEXT_HAND", "0") == "1"
        c.auto_rebuy = os.getenv("POKER_AUTO_REBUY", "0") == "1"
        c.rebuy_threshold = int(os.getenv("POKER_REBUY_THRESHOLD", "200"))
        raw_amount = os.getenv("POKER_REBUY_AMOUNT", "")
        c.rebuy_amount = int(raw_amount) if raw_amount else None

        c.llm_enabled = os.getenv("OPENAI_ENABLE", "0") == "1"
        c.llm_api_key = os.getenv("OPENAI_API_KEY", "")
        c.llm_base_url = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/")
        c.llm_model = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
        c.llm_timeout = float(os.getenv("OPENAI_TIMEOUT", "20"))
        c.llm_temperature = float(os.getenv("OPENAI_TEMPERATURE", "0.1"))
        return c

    def validate(self) -> None:
        if not self.base_url:
            sys.exit("POKER_BASE_URL is required")
        if not self.token:
            sys.exit("POKER_TOKEN is required")
        if not self.game_id:
            sys.exit("POKER_GAME_ID is required")


# ---------------------------------------------------------------------------
# Utility: deep_get
# ---------------------------------------------------------------------------

def deep_get(obj: Any, *keys: str, default: Any = None) -> Any:
    """Safely traverse nested dicts/lists by key or index."""
    cur = obj
    for k in keys:
        if cur is None:
            return default
        if isinstance(cur, dict):
            cur = cur.get(k)
        elif isinstance(k, int) and isinstance(cur, list):
            try:
                cur = cur[k]
            except IndexError:
                return default
        else:
            return default
    return cur if cur is not None else default


def first_of(d: dict, *keys: str, default: Any = None) -> Any:
    """Return the value of the first key found in dict d."""
    for k in keys:
        if k in d:
            return d[k]
    return default


# ---------------------------------------------------------------------------
# Card utilities
# ---------------------------------------------------------------------------

RANKS = "23456789TJQKA"
SUITS = "cdhs"   # clubs diamonds hearts spades
RANK_VAL: dict[str, int] = {r: i for i, r in enumerate(RANKS, 2)}

# [SCHEMA] CardModel has fields: value(int), rank(str), suit(str), display(str)
# value encodes rank and suit as in poker.py (likely 0-51 or similar).
# We use the 'rank' and 'suit' string fields for evaluation.

@dataclass(frozen=True)
class Card:
    rank: str   # one of RANKS
    suit: str   # one of SUITS

    @property
    def value(self) -> int:
        return RANK_VAL[self.rank]

    def __str__(self) -> str:
        return f"{self.rank}{self.suit}"


def parse_card_model(cm: dict) -> Card | None:
    """Parse a CardModel dict from the API into a Card."""
    # [SCHEMA] CardModel: {value, rank, suit, display}
    rank = cm.get("rank", "")
    suit = cm.get("suit", "")
    if not rank or not suit:
        # fallback: try display like "Ah", "Kd"
        display = cm.get("display", "")
        if len(display) >= 2:
            rank = display[0].upper()
            suit = display[1].lower()
    # Normalize rank: 10 → T
    if rank == "10":
        rank = "T"
    rank = rank.upper()
    suit = suit.lower()[:1]
    if rank in RANK_VAL and suit in SUITS:
        return Card(rank=rank, suit=suit)
    return None


def parse_cards(card_list: list[dict]) -> list[Card]:
    cards = []
    for cm in (card_list or []):
        c = parse_card_model(cm)
        if c:
            cards.append(c)
    return cards


def cards_str(cards: list[Card]) -> str:
    return " ".join(str(c) for c in cards) if cards else "[]"


# ---------------------------------------------------------------------------
# Hand evaluation (lightweight)
# ---------------------------------------------------------------------------

# Hand rank constants (higher = better)
HIGH_CARD      = 1
ONE_PAIR       = 2
TWO_PAIR       = 3
THREE_OF_KIND  = 4
STRAIGHT       = 5
FLUSH          = 6
FULL_HOUSE     = 7
FOUR_OF_KIND   = 8
STRAIGHT_FLUSH = 9

def _is_straight(vals: list[int]) -> bool:
    s = sorted(set(vals))
    # Normal straight
    for i in range(len(s) - 4):
        if s[i+4] - s[i] == 4 and len(s[i:i+5]) == 5:
            return True
    # Wheel: A-2-3-4-5
    if set([14, 2, 3, 4, 5]).issubset(set(vals)):
        return True
    return False


def evaluate_5(cards: list[Card]) -> int:
    """Return hand rank (1-9) for exactly 5 cards."""
    vals = [c.value for c in cards]
    suits = [c.suit for c in cards]
    is_flush = len(set(suits)) == 1
    is_str = _is_straight(vals)
    if is_flush and is_str:
        return STRAIGHT_FLUSH
    from collections import Counter
    cnt = Counter(vals)
    counts = sorted(cnt.values(), reverse=True)
    if counts[0] == 4:
        return FOUR_OF_KIND
    if counts[0] == 3 and counts[1] == 2:
        return FULL_HOUSE
    if is_flush:
        return FLUSH
    if is_str:
        return STRAIGHT
    if counts[0] == 3:
        return THREE_OF_KIND
    if counts[0] == 2 and counts[1] == 2:
        return TWO_PAIR
    if counts[0] == 2:
        return ONE_PAIR
    return HIGH_CARD


def best_hand_rank(hole: list[Card], board: list[Card]) -> int:
    """Best 5-card hand rank from hole + board (up to 7 cards)."""
    all_cards = hole + board
    if len(all_cards) < 5:
        # Can't make 5 — evaluate what we have heuristically
        if len(all_cards) == 0:
            return HIGH_CARD
        from collections import Counter
        vals = [c.value for c in all_cards]
        counts = sorted(Counter(vals).values(), reverse=True)
        if counts[0] >= 3:
            return THREE_OF_KIND
        if counts[0] == 2:
            return ONE_PAIR
        return HIGH_CARD
    best = 0
    for combo in combinations(all_cards, 5):
        r = evaluate_5(list(combo))
        if r > best:
            best = r
    return best


HAND_NAMES = {
    HIGH_CARD: "high card",
    ONE_PAIR: "one pair",
    TWO_PAIR: "two pair",
    THREE_OF_KIND: "three of a kind",
    STRAIGHT: "straight",
    FLUSH: "flush",
    FULL_HOUSE: "full house",
    FOUR_OF_KIND: "four of a kind",
    STRAIGHT_FLUSH: "straight flush",
}


# ---------------------------------------------------------------------------
# Board texture helpers
# ---------------------------------------------------------------------------

def has_flush_draw(hole: list[Card], board: list[Card]) -> bool:
    """True if hero has 4+ cards to a flush."""
    from collections import Counter
    all_cards = hole + board
    suit_counts = Counter(c.suit for c in all_cards)
    hero_suits = {c.suit for c in hole}
    for s, cnt in suit_counts.items():
        if s in hero_suits and cnt >= 4:
            return True
    return False


def has_oesd(hole: list[Card], board: list[Card]) -> bool:
    """True if hero has an open-ended straight draw (4 consecutive)."""
    all_cards = hole + board
    vals = sorted(set(c.value for c in all_cards))
    hero_vals = {c.value for c in hole}
    for i in range(len(vals) - 3):
        window = vals[i:i+4]
        if window[-1] - window[0] == 3 and len(window) == 4:
            if any(v in hero_vals for v in window):
                return True
    return False


def board_is_paired(board: list[Card]) -> bool:
    from collections import Counter
    return any(v >= 2 for v in Counter(c.value for c in board).values())


# ---------------------------------------------------------------------------
# Preflop hand classification
# ---------------------------------------------------------------------------

# Returns a tier 1 (premium) → 5 (garbage)
def preflop_tier(hole: list[Card]) -> int:
    """
    Tier 1: Premium  – AA, KK, QQ, AKs
    Tier 2: Strong   – JJ, TT, AKo, AQs, AJs, KQs
    Tier 3: Medium   – 99-77, AQo, AJs, KQo, KJs, QJs, JTs
    Tier 4: Speculative – 66-22, suited connectors, weak broadway
    Tier 5: Weak     – everything else
    """
    if len(hole) < 2:
        return 5
    r1, r2 = hole[0].rank, hole[1].rank
    s1, s2 = hole[0].suit, hole[1].suit
    v1, v2 = RANK_VAL[r1], RANK_VAL[r2]
    suited = s1 == s2
    # Normalize: high card first
    if v1 < v2:
        r1, r2 = r2, r1
        v1, v2 = v2, v1
    pair = v1 == v2

    if pair:
        if v1 >= RANK_VAL["Q"]:   return 1   # AA, KK, QQ
        if v1 >= RANK_VAL["T"]:   return 2   # JJ, TT
        if v1 >= RANK_VAL["7"]:   return 3   # 99-77
        return 4                              # 66-22

    # Ace-x
    if r1 == "A":
        if r2 == "K":
            return 1 if suited else 2
        if r2 in ("Q", "J"):
            return 2 if suited else 3
        if r2 in ("T", "9"):
            return 3 if suited else 4
        return 4 if suited else 5

    # KK already handled
    if r1 == "K":
        if r2 == "Q":
            return 2 if suited else 3
        if r2 == "J":
            return 3 if suited else 4
        return 4 if suited else 5

    if r1 == "Q":
        if r2 == "J":
            return 3 if suited else 4
        return 4 if suited else 5

    # Suited connectors / broadway
    gap = v1 - v2
    if suited and gap <= 2 and v1 >= RANK_VAL["8"]:
        return 4

    return 5


# ---------------------------------------------------------------------------
# API Client
# ---------------------------------------------------------------------------

class APIError(Exception):
    pass


class PokerAPIClient:
    """Thin wrapper around the poker server HTTP API."""

    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        self.session = requests.Session()
        self.session.headers.update({"Content-Type": "application/json"})

    # ---- low-level --------------------------------------------------------

    def _player_headers(self) -> dict:
        # [SCHEMA] Auth: X-Player-Token header
        return {"X-Player-Token": self.cfg.token}

    def _banker_headers(self) -> dict:
        return {"X-Player-Token": self.cfg.banker_token}

    def _get(self, path: str, headers: dict | None = None, timeout: float = 10) -> dict:
        url = f"{self.cfg.base_url}{path}"
        try:
            r = self.session.get(url, headers=headers, timeout=timeout)
        except requests.RequestException as e:
            raise APIError(f"GET {path} network error: {e}") from e
        if _verbose():
            LOG.debug("GET %s → %d", path, r.status_code)
        if not r.ok:
            raise APIError(f"GET {path} → HTTP {r.status_code}: {r.text[:200]}")
        return r.json()

    def _post(self, path: str, body: dict | None = None, headers: dict | None = None, timeout: float = 10) -> dict:
        url = f"{self.cfg.base_url}{path}"
        try:
            r = self.session.post(url, json=body or {}, headers=headers, timeout=timeout)
        except requests.RequestException as e:
            raise APIError(f"POST {path} network error: {e}") from e
        if _verbose():
            LOG.debug("POST %s %s → %d %s", path, body, r.status_code, r.text[:200])
        if not r.ok:
            raise APIError(f"POST {path} → HTTP {r.status_code}: {r.text[:200]}")
        return r.json()

    # ---- health / identity -----------------------------------------------

    def health(self) -> dict:
        return self._get("/")

    def get_me(self) -> dict:
        # [SCHEMA] GET /me → SessionRecoveryResponse: player_id, name, game_id, seat, role, status, chips
        return self._get("/me", headers=self._player_headers())

    # ---- game management -------------------------------------------------

    def list_games(self) -> dict:
        return self._get("/games")

    def get_game(self) -> dict:
        # [SCHEMA] GET /games/{id} → GameStateResponse
        return self._get(f"/games/{self.cfg.game_id}")

    def get_hand(self) -> dict:
        # [SCHEMA] GET /games/{id}/hand → HandResponse: player_id, hole_cards, community_cards
        return self._get(f"/games/{self.cfg.game_id}/hand", headers=self._player_headers())

    def get_players(self) -> dict:
        # [SCHEMA] GET /games/{id}/players → PlayerListResponse: players[], total_chips_in_play
        return self._get(f"/games/{self.cfg.game_id}/players")

    def get_history(self) -> dict:
        return self._get(f"/games/{self.cfg.game_id}/history")

    def join_game(self, player_name: str) -> dict:
        # [SCHEMA] POST /games/{id}/join  body: {player_name}
        # → JoinGameResponse: player_id, player_token, seat, starting_chips
        return self._post(f"/games/{self.cfg.game_id}/join", {"player_name": player_name})

    def start_game(self) -> dict:
        return self._post(f"/games/{self.cfg.game_id}/start", headers=self._banker_headers())

    def next_hand(self) -> dict:
        return self._post(f"/games/{self.cfg.game_id}/next-hand", headers=self._banker_headers())

    def leave_game(self) -> dict:
        return self._post(f"/games/{self.cfg.game_id}/leave", headers=self._player_headers())

    def sit_out(self) -> dict:
        return self._post(f"/games/{self.cfg.game_id}/sit-out", headers=self._player_headers())

    def sit_in(self) -> dict:
        return self._post(f"/games/{self.cfg.game_id}/sit-in", headers=self._player_headers())

    def rebuy(self) -> dict:
        return self._post(f"/games/{self.cfg.game_id}/rebuy", headers=self._player_headers())

    def action(self, action_type: str, amount: int | None = None) -> dict:
        # [SCHEMA] POST /games/{id}/action
        # body: PlayerActionRequest {action: ActionType, amount: int|None}
        # ActionType values: check, call, raise, fold, all_in
        # amount required for raise, must be omitted for check/fold/call/all_in
        body: dict[str, Any] = {"action": action_type}
        if amount is not None:
            body["amount"] = amount
        return self._post(f"/games/{self.cfg.game_id}/action", body, headers=self._player_headers())


# ---------------------------------------------------------------------------
# Game state snapshot
# ---------------------------------------------------------------------------

@dataclass
class GameSnapshot:
    # raw API payloads
    game_raw: dict = field(default_factory=dict)
    hand_raw: dict = field(default_factory=dict)
    players_raw: dict = field(default_factory=dict)
    me_raw: dict = field(default_factory=dict)

    # derived
    game_status: str = ""          # waiting / running / paused / finished
    street: str = ""               # pre_flop / flop / turn / river / showdown
    hand_number: int = 0
    pot: int = 0
    community: list[Card] = field(default_factory=list)
    hole: list[Card] = field(default_factory=list)

    my_player_id: str = ""
    my_name: str = ""
    my_chips: int = 0
    my_status: str = ""            # active / folded / all_in / sitting_out / eliminated
    my_role: str = ""              # player / banker
    my_seat: int = -1
    my_bet_this_street: int = 0

    current_player_id: str = ""
    is_my_turn: bool = False

    # [SCHEMA] TurnOptions: can_check, call_amount, min_raise, max_raise, can_fold
    can_check: bool = False
    call_amount: int = 0
    min_raise: int = 0
    max_raise: int = 0
    can_fold: bool = True

    small_blind: int = 10
    big_blind: int = 20
    allow_rebuy: bool = False

    players: list[dict] = field(default_factory=list)   # PlayerPublicView dicts
    active_player_count: int = 0


def build_snapshot(game: dict, hand: dict, players: dict, me: dict) -> GameSnapshot:
    """Parse raw API responses into a GameSnapshot."""
    s = GameSnapshot()
    s.game_raw = game
    s.hand_raw = hand
    s.players_raw = players
    s.me_raw = me

    # [SCHEMA] GameStateResponse fields
    s.game_status = game.get("status", "")
    s.street = game.get("street") or ""
    s.hand_number = game.get("hand_number", 0)
    s.pot = game.get("pot", 0)
    s.community = parse_cards(game.get("community_cards", []))
    s.current_player_id = game.get("current_player_id") or ""
    s.small_blind = game.get("small_blind", 10)
    s.big_blind = game.get("big_blind", 20)
    s.allow_rebuy = game.get("allow_rebuy", False)

    # Turn options embedded in game state
    # [SCHEMA] current_turn_options: TurnOptions | None
    opts = game.get("current_turn_options") or {}
    s.can_check = opts.get("can_check", False)
    s.call_amount = opts.get("call_amount", 0)
    s.min_raise = opts.get("min_raise", 0)
    s.max_raise = opts.get("max_raise", 0)
    s.can_fold = opts.get("can_fold", True)

    # [SCHEMA] HandResponse: player_id, hole_cards, community_cards
    s.hole = parse_cards(hand.get("hole_cards", []))

    # [SCHEMA] SessionRecoveryResponse: player_id, name, game_id, seat, role, status, chips
    s.my_player_id = me.get("player_id", "")
    s.my_name = me.get("name", "")
    s.my_chips = me.get("chips", 0)
    s.my_status = me.get("status", "")
    s.my_role = me.get("role", "player")
    s.my_seat = me.get("seat", -1)

    # [SCHEMA] PlayerListResponse: players[] of PlayerPublicView
    # PlayerPublicView: player_id, name, seat, chips, role, status, bet_this_street, is_current
    all_players = players.get("players", [])
    s.players = all_players
    for p in all_players:
        if p.get("player_id") == s.my_player_id:
            s.my_chips = p.get("chips", s.my_chips)
            s.my_status = p.get("status", s.my_status)
            s.my_bet_this_street = p.get("bet_this_street", 0)
            break

    active_statuses = {"active", "all_in"}
    s.active_player_count = sum(
        1 for p in all_players if p.get("status", "") in active_statuses
    )

    s.is_my_turn = bool(s.my_player_id and s.current_player_id == s.my_player_id)

    return s


def summarize_state(s: GameSnapshot) -> str:
    """Compact human-readable state summary."""
    lines = [
        f"Hand #{s.hand_number}  Street={s.street or 'none'}  Pot={s.pot}  Status={s.game_status}",
        f"Hero: {s.my_name} seat={s.my_seat} chips={s.my_chips} status={s.my_status} bet_street={s.my_bet_this_street}",
        f"Hole: {cards_str(s.hole)}  Board: {cards_str(s.community)}",
        f"Blinds: {s.small_blind}/{s.big_blind}  Players active: {s.active_player_count}",
    ]
    if s.is_my_turn:
        lines.append(
            f"MY TURN → can_check={s.can_check} call={s.call_amount} "
            f"raise_range=[{s.min_raise},{s.max_raise}] can_fold={s.can_fold}"
        )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Legal action set
# ---------------------------------------------------------------------------

def legal_actions(s: GameSnapshot) -> set[str]:
    """
    Derive canonical legal actions from the turn options.
    Canonical names: fold, check, call, bet, raise, all_in
    """
    acts: set[str] = set()
    if not s.is_my_turn:
        return acts

    # [SCHEMA] TurnOptions: can_check, call_amount, min_raise, max_raise, can_fold
    if s.can_fold:
        acts.add("fold")
    if s.can_check:
        acts.add("check")
    if s.call_amount > 0:
        acts.add("call")
    if s.min_raise > 0 and s.max_raise > 0:
        # Server accepts "raise" with an amount
        acts.add("raise")
    if s.my_chips > 0:
        acts.add("all_in")
    return acts


# ---------------------------------------------------------------------------
# Deterministic heuristic policy
# ---------------------------------------------------------------------------

@dataclass
class Decision:
    action: str
    amount: int | None
    source: str   # "heuristic" or "llm"
    reason: str = ""


def _pot_odds_ok(call: int, pot: int) -> bool:
    """Return True if pot odds justify a call (roughly ≥ 20% equity needed)."""
    if call <= 0:
        return True
    total = pot + call
    if total == 0:
        return True
    return call / total <= 0.40   # willing to call up to ~40% of pot+call


def _choose_raise(s: GameSnapshot, multiplier: float = 2.5) -> int:
    """Pick a raise size clamped to [min_raise, max_raise]."""
    size = int(s.pot * multiplier)
    size = max(size, s.min_raise)
    size = min(size, s.max_raise)
    return size


def heuristic_decision(s: GameSnapshot) -> Decision:
    """
    Layered deterministic poker strategy.
    Returns a validated Decision.
    """
    acts = legal_actions(s)
    if not acts:
        return Decision("fold", None, "heuristic", "no legal actions")

    hole = s.hole
    board = s.community
    street = s.street
    pot = s.pot or 1
    call = s.call_amount

    # Convenience helpers
    def can(a: str) -> bool:
        return a in acts

    def do_check_or_fold() -> Decision:
        if can("check"):
            return Decision("check", None, "heuristic", "weak hand, free check")
        return Decision("fold", None, "heuristic", "weak hand, fold to bet")

    def do_check_or_call() -> Decision:
        if can("check"):
            return Decision("check", None, "heuristic", "marginal, free check")
        if can("call") and _pot_odds_ok(call, pot):
            return Decision("call", None, "heuristic", "marginal, pot odds ok")
        return Decision("fold", None, "heuristic", "marginal, pot odds too wide")

    def do_raise(multiplier: float = 2.5, reason: str = "value") -> Decision:
        if can("raise"):
            amt = _choose_raise(s, multiplier)
            return Decision("raise", amt, "heuristic", reason)
        if can("call"):
            return Decision("call", None, "heuristic", reason + " (no raise, call)")
        if can("check"):
            return Decision("check", None, "heuristic", reason + " (check)")
        return Decision("all_in", None, "heuristic", reason + " (all-in)")

    def do_bet(reason: str = "value") -> Decision:
        # "bet" uses raise on this server; same mechanics
        return do_raise(2.0, reason)

    # -----------------------------------------------------------------------
    # PRE-FLOP
    # -----------------------------------------------------------------------
    if street in ("pre_flop", "") or not board:
        if len(hole) < 2:
            return do_check_or_fold()

        tier = preflop_tier(hole)

        if tier == 1:   # Premium: AA KK QQ AKs
            return do_raise(3.0, f"premium preflop {cards_str(hole)}")

        if tier == 2:   # Strong: JJ TT AKo AQs
            if call <= 3 * s.big_blind:
                return do_raise(2.5, f"strong preflop {cards_str(hole)}")
            return do_raise(2.5, f"strong preflop {cards_str(hole)}")

        if tier == 3:   # Medium: 99-77, AQo, AJs, KQo
            if call == 0:
                return do_bet("medium preflop")
            if call <= 2 * s.big_blind:
                return Decision("call", None, "heuristic", f"medium preflop call {cards_str(hole)}")
            if can("call") and call <= s.my_chips // 4:
                return Decision("call", None, "heuristic", "medium preflop marginal call")
            return do_check_or_fold()

        if tier == 4:   # Speculative: SC, small pairs
            if call == 0:
                return Decision("check", None, "heuristic", "speculative, free look")
            if call <= s.big_blind:
                return Decision("call", None, "heuristic", "speculative, cheap call")
            return do_check_or_fold()

        # tier 5: garbage
        return do_check_or_fold()

    # -----------------------------------------------------------------------
    # POST-FLOP (flop / turn / river)
    # -----------------------------------------------------------------------

    if len(hole) < 2:
        return do_check_or_fold()

    hand_rank = best_hand_rank(hole, board)
    flush_draw = has_flush_draw(hole, board)
    oesd = has_oesd(hole, board)
    strong_draw = flush_draw or oesd

    # Strong made hands: two pair or better
    if hand_rank >= TWO_PAIR:
        if hand_rank >= FULL_HOUSE:
            # Monster: raise big or push
            if can("raise") and s.max_raise >= s.min_raise:
                amt = s.max_raise  # overbet / push
                return Decision("raise", amt, "heuristic", f"monster {HAND_NAMES[hand_rank]}")
            return Decision("all_in", None, "heuristic", f"monster {HAND_NAMES[hand_rank]}")
        return do_raise(2.5, f"strong {HAND_NAMES[hand_rank]}")

    # One pair
    if hand_rank == ONE_PAIR:
        hole_vals = {c.value for c in hole}
        board_vals = [c.value for c in board]
        # Overpair: both hole cards beat all board cards
        board_max = max(board_vals) if board_vals else 0
        if all(v > board_max for v in hole_vals):
            return do_bet("overpair")
        # Top pair (one hole card matches highest board card)
        if any(v == board_max for v in hole_vals):
            if call == 0:
                return do_bet("top pair")
            if _pot_odds_ok(call, pot):
                return Decision("call", None, "heuristic", "top pair call")
            return Decision("fold", None, "heuristic", "top pair, price too high")
        # Under pair / middle pair
        if call == 0:
            return Decision("check", None, "heuristic", "middle pair check")
        if _pot_odds_ok(call * 2, pot):   # tighter on middle pair
            return Decision("call", None, "heuristic", "middle pair, ok pot odds")
        return Decision("fold", None, "heuristic", "middle pair, fold to big bet")

    # High card — but has draw
    if strong_draw:
        draw_type = "flush draw" if flush_draw else "OESD"
        if call == 0:
            return do_bet(f"semi-bluff {draw_type}")
        if _pot_odds_ok(call, pot):
            return Decision("call", None, "heuristic", f"draw call {draw_type}")
        return do_check_or_fold()

    # High card, no draw
    return do_check_or_fold()


# ---------------------------------------------------------------------------
# LLM-assisted policy
# ---------------------------------------------------------------------------

LLM_SYSTEM = (
    "You are a professional Texas Hold'em poker decision assistant. "
    "Your only job is to return valid JSON in this exact format:\n"
    '{"action": "<fold|check|call|bet|raise|all_in>", "amount": <integer or null>, "reason": "<short string>"}\n'
    "Rules:\n"
    "- Choose only from the listed legal_actions.\n"
    "- amount must be an integer in [min_raise, max_raise] when action is raise or bet, else null.\n"
    "- Be chip-EV oriented. Minimize leaks. Never bluff with no equity unless it is clearly profitable.\n"
    "- Return ONLY the JSON object. No prose, no markdown."
)


def build_llm_prompt(s: GameSnapshot, history_snippet: str = "") -> str:
    acts = sorted(legal_actions(s))
    hand_rank = best_hand_rank(s.hole, s.community) if s.hole else 0
    prompt_data = {
        "street": s.street,
        "hero_cards": cards_str(s.hole),
        "board": cards_str(s.community),
        "hand_strength": HAND_NAMES.get(hand_rank, "unknown"),
        "pot": s.pot,
        "hero_stack": s.my_chips,
        "hero_bet_this_street": s.my_bet_this_street,
        "amount_to_call": s.call_amount,
        "can_check": s.can_check,
        "legal_actions": acts,
        "min_raise": s.min_raise,
        "max_raise": s.max_raise,
        "blinds": f"{s.small_blind}/{s.big_blind}",
        "active_players": s.active_player_count,
    }
    if history_snippet:
        prompt_data["recent_history"] = history_snippet
    return json.dumps(prompt_data, indent=2)


def llm_decision(s: GameSnapshot, cfg: Config, history_snippet: str = "") -> Decision | None:
    """
    Call LLM and parse its response.
    Returns a validated Decision or None on any failure.
    """
    if not cfg.llm_enabled or not cfg.llm_api_key:
        return None

    prompt = build_llm_prompt(s, history_snippet)
    if _verbose():
        LOG.debug("LLM prompt:\n%s", prompt)

    url = f"{cfg.llm_base_url}/chat/completions"
    headers = {
        "Authorization": f"Bearer {cfg.llm_api_key}",
        "Content-Type": "application/json",
    }
    body = {
        "model": cfg.llm_model,
        "temperature": cfg.llm_temperature,
        "messages": [
            {"role": "system", "content": LLM_SYSTEM},
            {"role": "user", "content": prompt},
        ],
    }

    try:
        r = requests.post(url, json=body, headers=headers, timeout=cfg.llm_timeout)
        r.raise_for_status()
        resp = r.json()
    except Exception as e:
        LOG.warning("LLM request failed: %s", e)
        return None

    raw_content = deep_get(resp, "choices", 0, "message", "content", default="")
    if _verbose():
        LOG.debug("LLM raw response: %s", raw_content)

    # Strip possible markdown fences
    content = raw_content.strip()
    if content.startswith("```"):
        content = "\n".join(content.split("\n")[1:])
        content = content.rstrip("`").strip()

    try:
        parsed = json.loads(content)
    except json.JSONDecodeError:
        LOG.warning("LLM returned non-JSON: %s", content[:200])
        return None

    action = str(parsed.get("action", "")).lower().strip()
    amount = parsed.get("amount")
    reason = str(parsed.get("reason", "llm"))

    # Normalize: server uses "raise" not "bet"
    if action == "bet":
        action = "raise"

    acts = legal_actions(s)
    if action not in acts:
        LOG.warning("LLM chose illegal action '%s' (legal: %s)", action, acts)
        return None

    # Validate amount constraints
    if action == "raise":
        if not isinstance(amount, int):
            LOG.warning("LLM raise missing valid amount: %s", amount)
            return None
        amount = max(s.min_raise, min(s.max_raise, int(amount)))
    else:
        amount = None

    return Decision(action=action, amount=amount, source="llm", reason=reason)


# ---------------------------------------------------------------------------
# History snippet helper
# ---------------------------------------------------------------------------

def history_snippet(history: dict, max_actions: int = 10) -> str:
    """Return a compact string of recent actions from hand history."""
    # [SCHEMA] HandHistoryResponse: actions[] of ActionHistoryItem
    actions = (history or {}).get("actions", [])
    recent = actions[-max_actions:]
    parts = []
    for a in recent:
        name = a.get("player_name", a.get("player_id", "?"))
        atype = a.get("action_type", "?")
        amount = a.get("amount")
        street = a.get("street", "?")
        chunk = f"{street}/{name}:{atype}"
        if amount:
            chunk += f"({amount})"
        parts.append(chunk)
    return " ".join(parts)


# ---------------------------------------------------------------------------
# Bot core
# ---------------------------------------------------------------------------

class PokerBot:
    def __init__(self, cfg: Config, dry_run: bool = False) -> None:
        self.cfg = cfg
        self.dry_run = dry_run
        self.api = PokerAPIClient(cfg)
        self.me: dict = {}
        self._running = True
        self._last_hand_number = -1

    # ---- setup ------------------------------------------------------------

    def setup(self) -> None:
        """Verify connectivity, identity, join game, sit in if needed."""
        # Health check
        try:
            h = self.api.health()
            LOG.info("Server health: %s", h.get("status", "?"))
        except APIError as e:
            LOG.warning("Health check failed (continuing): %s", e)

        # Identity
        try:
            self.me = self.api.get_me()
            LOG.info(
                "Identity: %s (id=%s, game=%s, role=%s, chips=%d)",
                self.me.get("name"),
                self.me.get("player_id"),
                self.me.get("game_id"),
                self.me.get("role"),
                self.me.get("chips", 0),
            )
        except APIError:
            LOG.info("GET /me failed — attempting to join as new player")
            self._join()
            return

        # If already in a different game or no game, join target game
        existing_game = self.me.get("game_id", "")
        if existing_game != self.cfg.game_id:
            LOG.info("Joining game %s", self.cfg.game_id)
            self._join()
        else:
            LOG.info("Already in game %s", self.cfg.game_id)

        # Sit in if sitting out
        status = self.me.get("status", "")
        if status == "sitting_out":
            LOG.info("Sitting in...")
            self._retry(self.api.sit_in)

    def _join(self) -> None:
        try:
            resp = self.api.join_game(self.cfg.player_name)
            new_token = resp.get("player_token")
            if new_token:
                LOG.info(
                    "Joined as %s (seat=%d, chips=%d). NEW TOKEN: %s",
                    self.cfg.player_name,
                    resp.get("seat", -1),
                    resp.get("starting_chips", 0),
                    new_token,
                )
                LOG.warning(
                    "Update POKER_TOKEN=%s for future runs (current token doesn't exist yet).",
                    new_token,
                )
                self.cfg.token = new_token
                self.cfg.banker_token = new_token
                self.api.session.headers.pop("X-Player-Token", None)
                # Refresh identity
                self.me = self.api.get_me()
        except APIError as e:
            LOG.error("Failed to join game: %s", e)

    # ---- main loop --------------------------------------------------------

    def run(self, once: bool = False) -> None:
        """Main polling loop."""
        signal.signal(signal.SIGINT, self._handle_sigint)
        signal.signal(signal.SIGTERM, self._handle_sigint)

        self.setup()

        while self._running:
            try:
                self._tick()
            except APIError as e:
                LOG.warning("API error in tick: %s — retrying in %ss", e, self.cfg.poll_interval)
            except Exception as e:
                LOG.error("Unexpected error in tick: %s", e, exc_info=_verbose())

            if once:
                break
            time.sleep(self.cfg.poll_interval)

    def _tick(self) -> None:
        """One polling cycle: fetch state, decide, act."""
        game = self._retry(self.api.get_game)
        if game is None:
            return

        status = game.get("status", "")

        # Auto-start
        if status == "waiting" and self.cfg.auto_start:
            LOG.info("Game is waiting — attempting start")
            self._retry(self.api.start_game, swallow=True)
            return

        if status not in ("running", "paused"):
            LOG.debug("Game status=%s, waiting...", status)
            return

        # Fetch supporting state
        hand = self._retry(self.api.get_hand, swallow=True) or {}
        players = self._retry(self.api.get_players, swallow=True) or {}
        me_fresh = self._retry(self.api.get_me, swallow=True) or self.me

        snap = build_snapshot(game, hand, players, me_fresh)

        if _verbose():
            LOG.debug("\n%s", summarize_state(snap))

        # Auto-rebuy
        if self.cfg.auto_rebuy and snap.allow_rebuy:
            if snap.my_chips < self.cfg.rebuy_threshold and snap.my_status != "eliminated":
                LOG.info("Stack %d < threshold %d — rebuying", snap.my_chips, self.cfg.rebuy_threshold)
                self._retry(self.api.rebuy, swallow=True)

        # Detect hand end → auto next-hand
        hand_active = snap.street and snap.street not in ("", "showdown")
        if not hand_active:
            if self.cfg.auto_next_hand and self._last_hand_number == snap.hand_number:
                # We've seen this hand number already and it's done
                LOG.info("Hand %d ended — starting next hand", snap.hand_number)
                self._retry(self.api.next_hand, swallow=True)
            self._last_hand_number = snap.hand_number
            return

        self._last_hand_number = snap.hand_number

        if not snap.is_my_turn:
            return

        if snap.my_status in ("folded", "all_in", "sitting_out", "eliminated"):
            return

        # Decide
        self._decide_and_act(snap)

    def _decide_and_act(self, s: GameSnapshot) -> None:
        LOG.info(
            "My turn | Hand #%d %s | hole=%s board=%s pot=%d stack=%d call=%d",
            s.hand_number, s.street,
            cards_str(s.hole), cards_str(s.community),
            s.pot, s.my_chips, s.call_amount,
        )

        decision: Decision | None = None

        # Try LLM first
        if self.cfg.llm_enabled:
            try:
                hist = self._retry(self.api.get_history, swallow=True) or {}
                snippet = history_snippet(hist)
                decision = llm_decision(s, self.cfg, snippet)
                if decision:
                    LOG.info("LLM: %s(amount=%s) — %s", decision.action, decision.amount, decision.reason)
            except Exception as e:
                LOG.warning("LLM error: %s", e)

        # Fallback to heuristic
        if decision is None:
            decision = heuristic_decision(s)
            LOG.info("Heuristic: %s(amount=%s) — %s", decision.action, decision.amount, decision.reason)

        if self.dry_run:
            LOG.info("[DRY-RUN] Would submit: %s amount=%s", decision.action, decision.amount)
            return

        # Submit
        try:
            result = self.api.action(decision.action, decision.amount)
            LOG.info(
                "Action submitted: %s → new_chips=%d pot=%d next=%s",
                result.get("action", decision.action),
                result.get("new_chips", s.my_chips),
                result.get("pot", s.pot),
                result.get("next_player_id", "?"),
            )
        except APIError as e:
            LOG.error("Action failed: %s", e)
            # On failure, try check or fold as last resort
            if not self.dry_run:
                fallback = "check" if s.can_check else "fold"
                if fallback != decision.action:
                    LOG.warning("Trying fallback action: %s", fallback)
                    try:
                        self.api.action(fallback)
                    except APIError as e2:
                        LOG.error("Fallback action also failed: %s", e2)

    # ---- utilities --------------------------------------------------------

    def _retry(self, fn, retries: int = 3, delay: float = 1.0, swallow: bool = False):
        """Call fn with simple exponential backoff. Returns result or None."""
        for attempt in range(retries):
            try:
                return fn()
            except APIError as e:
                if attempt == retries - 1:
                    if swallow:
                        LOG.debug("Swallowed API error after %d retries: %s", retries, e)
                        return None
                    raise
                wait = delay * (2 ** attempt) * (0.8 + 0.4 * random.random())
                LOG.debug("Retry %d/%d in %.1fs: %s", attempt + 1, retries, wait, e)
                time.sleep(wait)
        return None

    def _handle_sigint(self, *_) -> None:
        LOG.info("Shutting down gracefully...")
        self._running = False


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Autonomous Texas Hold'em bot",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Environment variables:
  POKER_BASE_URL       Server base URL (default: http://localhost:8000)
  POKER_TOKEN          Player token (X-Player-Token header)
  POKER_BANKER_TOKEN   Banker token for start/next-hand (defaults to POKER_TOKEN)
  POKER_GAME_ID        Target game ID
  POKER_PLAYER_NAME    Bot player name for new joins (default: PokerBot)
  POKER_POLL_INTERVAL  Poll interval seconds (default: 2)
  POKER_AUTO_START     "1" to auto-start waiting games
  POKER_AUTO_NEXT_HAND "1" to auto-advance to next hand
  POKER_AUTO_REBUY     "1" to auto-rebuy when stack is low
  POKER_REBUY_THRESHOLD Chip threshold for auto-rebuy (default: 200)
  POKER_VERBOSE        "1" for verbose logging

  OPENAI_ENABLE        "1" to enable LLM decisions
  OPENAI_API_KEY       LLM API key
  OPENAI_BASE_URL      LLM base URL (default: https://api.openai.com/v1)
  OPENAI_MODEL         LLM model name (default: gpt-4o-mini)
  OPENAI_TIMEOUT       LLM request timeout seconds (default: 20)
  OPENAI_TEMPERATURE   LLM temperature (default: 0.1)
""",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Evaluate state and act at most once, then exit (useful for testing)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Compute decisions but do not submit actions to the server",
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Enable verbose logging (same as POKER_VERBOSE=1)",
    )
    args = parser.parse_args()

    if args.verbose:
        os.environ["POKER_VERBOSE"] = "1"

    LOG.setLevel(logging.DEBUG if _verbose() else logging.INFO)

    cfg = Config.from_env()
    cfg.validate()

    LOG.info(
        "Starting PokerBot | game=%s | llm=%s | dry_run=%s | once=%s",
        cfg.game_id,
        "enabled" if cfg.llm_enabled else "disabled",
        args.dry_run,
        args.once,
    )

    bot = PokerBot(cfg, dry_run=args.dry_run)
    bot.run(once=args.once)


if __name__ == "__main__":
    main()
