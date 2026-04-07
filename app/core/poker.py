"""
Pure poker logic: card encoding, deck operations, hand evaluation.
No database or async — safe to unit test directly.

Card encoding: integer 0-51
  rank = card // 4    (0=2, 1=3, ..., 8=T, 9=J, 10=Q, 11=K, 12=A)
  suit = card % 4     (0=clubs, 1=diamonds, 2=hearts, 3=spades)
"""

from __future__ import annotations

import random
from collections import Counter
from itertools import combinations

RANK_CHARS = ["2", "3", "4", "5", "6", "7", "8", "9", "T", "J", "Q", "K", "A"]
SUIT_CHARS = ["c", "d", "h", "s"]
SUIT_NAMES = ["Clubs", "Diamonds", "Hearts", "Spades"]
RANK_NAMES = ["Two", "Three", "Four", "Five", "Six", "Seven", "Eight",
              "Nine", "Ten", "Jack", "Queen", "King", "Ace"]

HAND_RANK_NAMES = [
    "High Card", "One Pair", "Two Pair", "Three of a Kind",
    "Straight", "Flush", "Full House", "Four of a Kind", "Straight Flush",
]


def card_rank(c: int) -> int:
    return c // 4


def card_suit(c: int) -> int:
    return c % 4


def card_display(c: int) -> str:
    return RANK_CHARS[card_rank(c)] + SUIT_CHARS[card_suit(c)]


def card_to_dict(c: int) -> dict:
    return {
        "value": c,
        "rank": RANK_CHARS[card_rank(c)],
        "suit": SUIT_CHARS[card_suit(c)],
        "display": card_display(c),
    }


def new_shuffled_deck() -> list[int]:
    deck = list(range(52))
    random.shuffle(deck)
    return deck


def deal_cards(deck: list[int], n: int) -> tuple[list[int], list[int]]:
    """Returns (dealt_cards, remaining_deck). Does not mutate the input list."""
    return deck[:n], deck[n:]


# ---------------------------------------------------------------------------
# Hand evaluation
# ---------------------------------------------------------------------------

def evaluate_five(cards: list[int]) -> tuple[int, tuple]:
    """
    Evaluate exactly 5 cards.
    Returns (hand_rank, tiebreak) where hand_rank is 0-8 and
    tiebreak is a tuple of rank ints used for tie-breaking.
    Higher is better; Python tuple comparison works directly.
    """
    ranks = sorted([card_rank(c) for c in cards], reverse=True)
    suits = [card_suit(c) for c in cards]
    rank_counts = Counter(ranks)
    # Sort by (count desc, rank desc) for easy pattern matching
    counts_sorted = sorted(rank_counts.items(), key=lambda x: (x[1], x[0]), reverse=True)

    is_flush = len(set(suits)) == 1
    rank_set = set(ranks)
    is_normal_straight = len(rank_counts) == 5 and (max(ranks) - min(ranks) == 4)
    # Wheel: A-2-3-4-5 (Ace plays low)
    is_wheel = rank_set == {12, 0, 1, 2, 3}
    is_straight = is_normal_straight or is_wheel

    if is_straight and is_flush:
        top = 3 if is_wheel else max(ranks)  # wheel top = 5 (rank index 3)
        return (8, (top,))

    if counts_sorted[0][1] == 4:
        quad_rank = counts_sorted[0][0]
        kicker = counts_sorted[1][0]
        return (7, (quad_rank, kicker))

    if counts_sorted[0][1] == 3 and counts_sorted[1][1] == 2:
        return (6, (counts_sorted[0][0], counts_sorted[1][0]))

    if is_flush:
        return (5, tuple(ranks))

    if is_straight:
        top = 3 if is_wheel else max(ranks)
        return (4, (top,))

    if counts_sorted[0][1] == 3:
        trip_rank = counts_sorted[0][0]
        kickers = sorted([r for r in ranks if r != trip_rank], reverse=True)
        return (3, (trip_rank, *kickers))

    if counts_sorted[0][1] == 2 and counts_sorted[1][1] == 2:
        p1, p2 = sorted([counts_sorted[0][0], counts_sorted[1][0]], reverse=True)
        kicker = max(r for r in ranks if r not in (p1, p2))
        return (2, (p1, p2, kicker))

    if counts_sorted[0][1] == 2:
        pair_rank = counts_sorted[0][0]
        kickers = sorted([r for r in ranks if r != pair_rank], reverse=True)
        return (1, (pair_rank, *kickers))

    return (0, tuple(ranks))


def evaluate_best_hand(cards: list[int]) -> tuple[tuple[int, tuple], list[int]]:
    """
    Given 5-7 cards, return (best_score, best_five_cards).
    best_score = (hand_rank, tiebreak) from evaluate_five.
    """
    if len(cards) < 5:
        raise ValueError(f"Need at least 5 cards, got {len(cards)}")
    best_score: tuple[int, tuple] | None = None
    best_five: list[int] = []
    for combo in combinations(cards, 5):
        score = evaluate_five(list(combo))
        if best_score is None or score > best_score:
            best_score = score
            best_five = list(combo)
    return best_score, best_five  # type: ignore[return-value]


def describe_hand(hand_rank: int, tiebreak: tuple) -> str:
    """Return a human-readable hand description."""
    name = HAND_RANK_NAMES[hand_rank]
    if hand_rank == 8:  # Straight flush
        top = tiebreak[0]
        if top == 12:
            return "Royal Flush"
        return f"Straight Flush, {RANK_NAMES[top]}-high"
    if hand_rank == 7:
        return f"Four of a Kind, {RANK_NAMES[tiebreak[0]]}s"
    if hand_rank == 6:
        return f"Full House, {RANK_NAMES[tiebreak[0]]}s over {RANK_NAMES[tiebreak[1]]}s"
    if hand_rank == 5:
        return f"Flush, {RANK_NAMES[tiebreak[0]]}-high"
    if hand_rank == 4:
        top = tiebreak[0]
        if top == 3:
            return "Straight, Five-high (Wheel)"
        return f"Straight, {RANK_NAMES[top]}-high"
    if hand_rank == 3:
        return f"Three of a Kind, {RANK_NAMES[tiebreak[0]]}s"
    if hand_rank == 2:
        return f"Two Pair, {RANK_NAMES[tiebreak[0]]}s and {RANK_NAMES[tiebreak[1]]}s"
    if hand_rank == 1:
        return f"One Pair, {RANK_NAMES[tiebreak[0]]}s"
    return f"High Card, {RANK_NAMES[tiebreak[0]]}"
