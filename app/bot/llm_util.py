from __future__ import annotations

import json
import requests 

from app.bot.card_util import HAND_NAMES, best_hand_rank, cards_str, deep_get, legal_actions, GameSnapshot
from app.bot.config import LOG, Config, _verbose
from app.bot.core import Decision


# ---------------------------------------------------------------------------
# Style parameters
# ---------------------------------------------------------------------------

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
    "- The prompt includes a 'style' field — honour it:\n"
    "    aggressive: wide range, large raises, semi-bluff draws freely\n"
    "    mild: balanced TAG; value-bet, fold weak hands to big bets\n"
    "    passive: tight range, small raises, call/check rather than bluff\n"
    "- Be chip-EV oriented. Minimize leaks.\n"
    "- Return ONLY the JSON object. No prose, no markdown."
)


def build_llm_prompt(s: GameSnapshot, history_snippet: str = "", style: str = "mild") -> str:
    acts = sorted(legal_actions(s))
    hand_rank = best_hand_rank(s.hole, s.community) if s.hole else 0
    prompt_data = {
        "style": style,
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

    prompt = build_llm_prompt(s, history_snippet, cfg.style)
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

