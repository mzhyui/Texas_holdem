
from __future__ import annotations

import random
import signal
import time

from app.bot.card_util import cards_str, GameSnapshot, build_snapshot, summarize_state
from app.bot.config import LOG, Config, _verbose
from app.bot.core import Decision
from app.bot.heuristic_util import heuristic_decision
from app.bot.llm_util import history_snippet, llm_decision
from app.bot.poker_api import APIError, PokerAPIClient 



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

    def _leave(self) -> None:
        try:
            self.api.leave_game()
            LOG.info("Left game successfully.")
        except APIError as e:
            LOG.error("Failed to leave game: %s", e)

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
            decision = heuristic_decision(s, self.cfg.style)
            LOG.info("Heuristic[%s]: %s(amount=%s) — %s", self.cfg.style, decision.action, decision.amount, decision.reason)

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
        self._leave()
        self._running = False
