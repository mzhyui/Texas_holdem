from __future__ import annotations


from app.bot.card_util import FULL_HOUSE, HAND_NAMES, ONE_PAIR, TWO_PAIR, best_hand_rank, cards_str, has_flush_draw, has_oesd, legal_actions, preflop_tier, GameSnapshot
from app.bot.core import STYLE_PARAMS, Decision 

# ---------------------------------------------------------------------------
# Deterministic heuristic policy
# ---------------------------------------------------------------------------



def _choose_raise(s: GameSnapshot, multiplier: float = 2.5) -> int:
    """Pick a raise size clamped to [min_raise, max_raise]."""
    size = int(s.pot * multiplier)
    size = max(size, s.min_raise)
    size = min(size, s.max_raise)
    return size


def heuristic_decision(s: GameSnapshot, style: str = "mild") -> Decision:
    """
    Layered deterministic poker strategy, parameterised by play style.

    Styles:
      aggressive — wide range, large raises, semi-bluffs, high call tolerance
      mild       — balanced TAG; value-bets, disciplined folds (default)
      passive    — tight range, small raises, avoids bluffs, low call tolerance
    """
    sp = STYLE_PARAMS.get(style, STYLE_PARAMS["mild"])
    acts = legal_actions(s)
    if not acts:
        return Decision("fold", None, "heuristic", "no legal actions")

    hole = s.hole
    board = s.community
    street = s.street
    pot = s.pot or 1
    call = s.call_amount

    def can(a: str) -> bool:
        return a in acts

    def do_check_or_fold() -> Decision:
        if can("check"):
            return Decision("check", None, "heuristic", "weak hand, free check")
        return Decision("fold", None, "heuristic", "weak hand, fold to bet")

    def do_raise(multiplier: float | None = None, reason: str = "value") -> Decision:
        mult = multiplier if multiplier is not None else sp.value_raise_mult
        if can("raise"):
            amt = _choose_raise(s, mult)
            return Decision("raise", amt, "heuristic", reason)
        if can("call"):
            return Decision("call", None, "heuristic", reason + " (no raise, call)")
        if can("check"):
            return Decision("check", None, "heuristic", reason + " (check)")
        return Decision("all_in", None, "heuristic", reason + " (all-in)")

    # -----------------------------------------------------------------------
    # PRE-FLOP
    # -----------------------------------------------------------------------
    if street in ("pre_flop", "") or not board:
        if len(hole) < 2:
            return do_check_or_fold()

        tier = preflop_tier(hole)

        # Open-raise range: tiers 1..preflop_open_tier
        if tier <= sp.preflop_open_tier:
            mult = sp.open_raise_mult * 1.2 if tier == 1 else sp.open_raise_mult
            return do_raise(mult, f"tier{tier} preflop {cards_str(hole)}")

        # Limp / call range: tiers preflop_open_tier+1..preflop_limp_tier
        if tier <= sp.preflop_limp_tier:
            if call == 0:
                return Decision("check", None, "heuristic", "speculative, free look")
            if call <= s.big_blind:
                return Decision("call", None, "heuristic", "speculative, cheap limp")
            if sp.preflop_call_tier >= tier and call <= s.my_chips // 4:
                return Decision("call", None, "heuristic", f"tier{tier} marginal call")
            return do_check_or_fold()

        # Garbage
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

    # Monster hands: full house or better
    if hand_rank >= FULL_HOUSE:
        if sp.monster_overbet and can("raise") and s.max_raise >= s.min_raise:
            return Decision("raise", s.max_raise, "heuristic", f"monster {HAND_NAMES[hand_rank]}")
        return Decision("all_in", None, "heuristic", f"monster {HAND_NAMES[hand_rank]}")

    # Strong made hands: two pair or better (below full house)
    if hand_rank >= TWO_PAIR:
        return do_raise(sp.value_raise_mult, f"strong {HAND_NAMES[hand_rank]}")

    # One pair
    if hand_rank == ONE_PAIR:
        hole_vals = {c.value for c in hole}
        board_vals = [c.value for c in board]
        board_max = max(board_vals) if board_vals else 0
        # Overpair: both hole cards beat all board cards
        if all(v > board_max for v in hole_vals):
            return do_raise(sp.value_raise_mult, "overpair")
        # Top pair
        if any(v == board_max for v in hole_vals):
            if call == 0:
                return do_raise(sp.value_raise_mult, "top pair")
            if call / (pot + call) <= sp.call_threshold:
                return Decision("call", None, "heuristic", "top pair call")
            return Decision("fold", None, "heuristic", "top pair, price too high")
        # Middle/under pair — tighter threshold
        if call == 0:
            return Decision("check", None, "heuristic", "middle pair check")
        if call / (pot + call) <= sp.call_threshold * 0.7:
            return Decision("call", None, "heuristic", "middle pair, ok pot odds")
        return Decision("fold", None, "heuristic", "middle pair, fold to big bet")

    # Draw hands
    if strong_draw:
        draw_type = "flush draw" if flush_draw else "OESD"
        if sp.bluff_draws:
            if call == 0:
                return do_raise(sp.open_raise_mult, f"semi-bluff {draw_type}")
            if call / (pot + call) <= sp.draw_call_threshold:
                return Decision("call", None, "heuristic", f"draw call {draw_type}")
        else:
            if call == 0:
                return Decision("check", None, "heuristic", f"passive draw check {draw_type}")
            if call / (pot + call) <= sp.draw_call_threshold:
                return Decision("call", None, "heuristic", f"passive draw call {draw_type}")
        return do_check_or_fold()

    # High card, no draw
    return do_check_or_fold()
