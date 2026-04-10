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

Bot styles (POKER_BOT_STYLE)
-----------------------------
  aggressive  — plays wide preflop, raises large, bluffs draws, re-raises often
  mild        — (default) balanced TAG play; value-bets, folds weak draws to big bets
  passive     — calls more, raises rarely, avoids confrontation unless very strong

Schema assumptions (marked [SCHEMA])
-------------------------------------
All assumptions are derived from app/models/schemas.py and app/models/db.py.
If the server is ever updated, search for [SCHEMA] to find every assumption.
"""

from __future__ import annotations

import argparse
import logging
import os

from app.bot.bot import LOG, Config, PokerBot
from app.bot.config import VALID_STYLES, _verbose

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
  POKER_BOT_STYLE      Play style: aggressive | mild | passive (default: mild)
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
        "--style",
        choices=list(VALID_STYLES),
        default=None,
        help="Play style (overrides POKER_BOT_STYLE env var)",
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Enable verbose logging (same as POKER_VERBOSE=1)",
    )
    args = parser.parse_args()

    if args.verbose:
        os.environ["POKER_VERBOSE"] = "1"
    if args.style:
        os.environ["POKER_BOT_STYLE"] = args.style

    LOG.setLevel(logging.DEBUG if _verbose() else logging.INFO)

    cfg = Config.from_env()
    cfg.validate()

    LOG.info(
        "Starting PokerBot | game=%s | style=%s | llm=%s | dry_run=%s | once=%s",
        cfg.game_id,
        cfg.style,
        "enabled" if cfg.llm_enabled else "disabled",
        args.dry_run,
        args.once,
    )

    bot = PokerBot(cfg, dry_run=args.dry_run)
    bot.run(once=args.once)


if __name__ == "__main__":
    main()
