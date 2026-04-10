from __future__ import annotations

from dataclasses import dataclass
from itertools import combinations
from typing import Any

from dataclasses import dataclass, field

# ---------------------------------------------------------------------------
# Utility: deep_get
# ---------------------------------------------------------------------------

def deep_get(obj: Any, *keys, default: Any = None) -> Any:
    """Safely traverse nested dicts/lists by key or integer index."""
    cur = obj
    for k in keys:
        if cur is None:
            return default
        if isinstance(cur, list):
            if isinstance(k, int):
                try:
                    cur = cur[k]
                except IndexError:
                    return default
            else:
                return default
        elif isinstance(cur, dict):
            cur = cur.get(k)
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


