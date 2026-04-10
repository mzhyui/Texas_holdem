from __future__ import annotations

from dataclasses import dataclass

@dataclass
class Decision:
    action: str
    amount: int | None
    source: str   # "heuristic" or "llm"
    reason: str = ""

@dataclass
class StyleParams:
    """
    Knobs that differ between bot styles.

    preflop_open_tier      — open-raise when tier <= this (1=premium only … 4=speculative)
    preflop_call_tier      — call a raise when tier <= this
    preflop_limp_tier      — limp/call 1BB when tier <= this (rest fold)
    open_raise_mult        — pot multiplier for open raises
    value_raise_mult       — pot multiplier when we have a strong made hand
    bluff_draws            — True: semi-bluff draws; False: just call/check draws
    draw_call_threshold    — max fraction of pot+call we'll pay to draw (pot odds)
    call_threshold         — max fraction of pot+call we'll pay generally
    monster_overbet        — True: shove max with monsters; False: standard raise
    three_bet_tiers        — open-raise into a raise for tiers <= this
"""
    preflop_open_tier: int
    preflop_call_tier: int
    preflop_limp_tier: int
    open_raise_mult: float
    value_raise_mult: float
    bluff_draws: bool
    draw_call_threshold: float
    call_threshold: float
    monster_overbet: bool
    three_bet_tiers: int


STYLE_PARAMS: dict[str, StyleParams] = {
    "aggressive": StyleParams(
        preflop_open_tier=4,    # open with anything tier 1-4
        preflop_call_tier=3,    # call 3-bets with tier 1-3
        preflop_limp_tier=4,    # limp speculative hands instead of folding
        open_raise_mult=3.5,
        value_raise_mult=3.0,
        bluff_draws=True,
        draw_call_threshold=0.55,   # call up to 55% of pot+call
        call_threshold=0.50,
        monster_overbet=True,
        three_bet_tiers=2,
    ),
    "mild": StyleParams(
        preflop_open_tier=3,    # open tier 1-3
        preflop_call_tier=2,    # call re-raises only with tier 1-2
        preflop_limp_tier=3,    # limp tier 3 if free/cheap
        open_raise_mult=2.5,
        value_raise_mult=2.5,
        bluff_draws=True,
        draw_call_threshold=0.40,
        call_threshold=0.40,
        monster_overbet=True,
        three_bet_tiers=1,
    ),
    "passive": StyleParams(
        preflop_open_tier=2,    # only open with very strong hands
        preflop_call_tier=2,    # call raises with strong hands
        preflop_limp_tier=4,    # limp/call anything speculative
        open_raise_mult=2.0,
        value_raise_mult=2.0,
        bluff_draws=False,
        draw_call_threshold=0.30,
        call_threshold=0.35,
        monster_overbet=False,
        three_bet_tiers=1,
    ),
}

