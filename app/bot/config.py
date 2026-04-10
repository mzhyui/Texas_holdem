
from __future__ import annotations

import logging
import os
import sys
from dataclasses import dataclass

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

VALID_STYLES = {"aggressive", "mild", "passive"}


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
    style: str = "mild"       # aggressive | mild | passive

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
        c.style = os.getenv("POKER_BOT_STYLE", "mild").lower()

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
        if self.style not in VALID_STYLES:
            sys.exit(f"POKER_BOT_STYLE must be one of: {', '.join(sorted(VALID_STYLES))}")
