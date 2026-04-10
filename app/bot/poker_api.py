from __future__ import annotations

from typing import Any
import requests

from app.bot.config import LOG, Config, _verbose 

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

