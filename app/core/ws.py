"""
WebSocket connection manager.

Tracks per-game connections as (WebSocket, player_id | None) pairs.
player_id is None for unauthenticated (spectator) connections.
"""

from __future__ import annotations

from collections import defaultdict

from fastapi import WebSocket


class ConnectionManager:
    def __init__(self) -> None:
        self._connections: dict[str, list[tuple[WebSocket, str | None]]] = defaultdict(list)

    async def connect(self, game_id: str, ws: WebSocket, player_id: str | None) -> None:
        await ws.accept()
        self._connections[game_id].append((ws, player_id))

    def disconnect(self, game_id: str, ws: WebSocket) -> None:
        self._connections[game_id] = [
            (w, p) for w, p in self._connections[game_id] if w is not ws
        ]

    async def _send(self, ws: WebSocket, game_id: str, payload: dict) -> None:
        try:
            await ws.send_json(payload)
        except Exception:
            self.disconnect(game_id, ws)

    async def broadcast(self, game_id: str, payload: dict) -> None:
        for ws, _ in list(self._connections[game_id]):
            await self._send(ws, game_id, payload)

    async def send_to_player(self, game_id: str, player_id: str, payload: dict) -> None:
        for ws, pid in list(self._connections[game_id]):
            if pid == player_id:
                await self._send(ws, game_id, payload)


manager = ConnectionManager()
