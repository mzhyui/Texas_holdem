"""
Structured game logger for Texas Hold'em server.

Writes to logs/poker_game.log (rotating, max 10 MB × 5 backups).
Each log line is prefixed with [game=<uuid> hand=<N>] for easy grep.

Usage:
    from app.core.game_logger import game_log
    game_log.hand_start(game, players)
    game_log.action(game, player, action_type, amount)
    game_log.street_advance(game, new_cards)
    game_log.showdown_result(game, results)
    game_log.fold_win(game, winner, pot)
"""

from __future__ import annotations

import logging
import os
from logging.handlers import RotatingFileHandler
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.models.db import ActionType, Game, Player

_LOG_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "logs")
_LOG_FILE = os.path.join(_LOG_DIR, "poker_game.log")

_logger = logging.getLogger("poker_game")


def setup_game_logger() -> None:
    """Configure the poker_game logger with a rotating file handler.
    Call once from app lifespan on startup."""
    if _logger.handlers:
        return  # already set up

    os.makedirs(_LOG_DIR, exist_ok=True)

    handler = RotatingFileHandler(
        _LOG_FILE,
        maxBytes=10 * 1024 * 1024,  # 10 MB
        backupCount=5,
        encoding="utf-8",
    )
    handler.setFormatter(logging.Formatter(
        fmt="%(asctime)s %(levelname)s %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    ))
    _logger.addHandler(handler)
    _logger.setLevel(logging.INFO)
    _logger.propagate = False


def _prefix(game_id: str, hand_number: int) -> str:
    return f"[game={game_id} hand={hand_number}]"


def _street_name(street) -> str:
    """Return street name whether street is a Street enum or a plain string."""
    if street is None:
        return "?"
    return street.value if hasattr(street, "value") else str(street)


def _fmt_cards(cards: list[int] | None) -> str:
    if not cards:
        return "[]"
    from app.core.poker import card_display
    return "[" + " ".join(card_display(c) for c in cards) + "]"


class _GameLog:
    """Thin facade over the poker_game logger with typed helpers."""

    # ------------------------------------------------------------------
    # Hand lifecycle
    # ------------------------------------------------------------------

    def hand_start(self, game: Game, players: list[Player]) -> None:
        """Log the beginning of a new hand."""
        dealer_player = next((p for p in players if p.seat == game.dealer_seat), None)
        dealer_id = dealer_player.id if dealer_player else f"seat{game.dealer_seat}"

        sb_idx = None
        bb_idx = None
        active = sorted(players, key=lambda p: p.seat)
        if len(active) >= 2:
            if len(active) == 2:
                # heads-up: dealer=SB
                sb_idx = next((i for i, p in enumerate(active) if p.seat == game.dealer_seat), None)
                bb_idx = 1 - sb_idx if sb_idx is not None else None
            else:
                dealer_i = next((i for i, p in enumerate(active) if p.seat == game.dealer_seat), 0)
                sb_idx = (dealer_i + 1) % len(active)
                bb_idx = (dealer_i + 2) % len(active)

        player_strs = []
        for i, p in enumerate(active):
            role = ""
            if i == sb_idx:
                role = "(SB)"
            elif i == bb_idx:
                role = "(BB)"
            hole = _fmt_cards(p.hole_cards) if p.hole_cards else "[]"
            player_strs.append(f"{p.id}{role}@seat{p.seat}={p.chips}chips hole={hole}")

        _logger.info(
            "%s HAND_START dealer=%s blinds=%d/%d players=[%s]",
            _prefix(game.id, game.hand_number),
            dealer_id,
            game.small_blind,
            game.big_blind,
            ", ".join(player_strs),
        )

    def blind(self, game: Game, player: Player, amount: int, blind_type: str) -> None:
        """Log a blind post (SB or BB)."""
        _logger.info(
            "%s BLIND %s=%s amount=%d pot_after=%d",
            _prefix(game.id, game.hand_number),
            blind_type,
            player.id,
            amount,
            game.pot,
        )

    # ------------------------------------------------------------------
    # Betting actions
    # ------------------------------------------------------------------

    def action(
        self,
        game: Game,
        player: Player,
        action_type: ActionType,
        amount: int | None,
    ) -> None:
        """Log a player bet/action."""
        amount_str = f" amount={amount}" if amount is not None else ""
        _logger.info(
            "%s ACTION street=%s player=%s action=%s%s chips_after=%d pot=%d",
            _prefix(game.id, game.hand_number),
            _street_name(game.current_street),
            player.id,
            action_type.value,
            amount_str,
            player.chips,
            game.pot,
        )

    # ------------------------------------------------------------------
    # Street transitions
    # ------------------------------------------------------------------

    def street_advance(self, game: Game, new_cards: list[int]) -> None:
        """Log a street transition and any newly dealt community cards."""
        community_all = game.community_cards
        _logger.info(
            "%s STREET street=%s new_cards=%s community=%s pot=%d",
            _prefix(game.id, game.hand_number),
            _street_name(game.current_street),
            _fmt_cards(new_cards),
            _fmt_cards(community_all),
            game.pot,
        )

    # ------------------------------------------------------------------
    # Hand results
    # ------------------------------------------------------------------

    def showdown_result(
        self,
        game: Game,
        results: list[dict],
    ) -> None:
        """Log each player's showdown result.

        results: list of dicts with keys:
            id, hole_cards (list[int]), best_hand (list[int]),
            hand_description (str|None), pot_won (int), chips_after (int)
        """
        _logger.info(
            "%s SHOWDOWN community=%s",
            _prefix(game.id, game.hand_number),
            _fmt_cards(game.community_cards),
        )
        for r in results:
            _logger.info(
                "%s RESULT player=%s hole=%s best=%s hand=%s pot_won=%d chips=%d",
                _prefix(game.id, game.hand_number),
                r["id"],
                _fmt_cards(r.get("hole_cards")),
                _fmt_cards(r.get("best_hand")),
                r.get("hand_description") or "—",
                r.get("pot_won", 0),
                r.get("chips_after", 0),
            )

    def fold_win(self, game: Game, winner: Player, pot: int) -> None:
        """Log a hand won when all opponents folded (no showdown)."""
        _logger.info(
            "%s FOLD_WIN winner=%s pot_won=%d chips=%d",
            _prefix(game.id, game.hand_number),
            winner.id,
            pot,
            winner.chips,
        )

    def timeout_action(
        self,
        game: Game,
        player: Player,
        action_type: ActionType,
    ) -> None:
        """Log an auto-action triggered by turn timer expiry."""
        _logger.info(
            "%s TIMEOUT street=%s player=%s auto_action=%s chips=%d",
            _prefix(game.id, game.hand_number),
            _street_name(game.current_street),
            player.id,
            action_type.value,
            player.chips,
        )


game_log = _GameLog()
