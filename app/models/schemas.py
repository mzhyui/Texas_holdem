from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field, model_validator

from app.models.db import ActionType


class CreateGameRequest(BaseModel):
    banker_name: str = Field(min_length=1, max_length=64)
    min_players: int = Field(ge=2, le=9, default=2)
    max_players: int = Field(ge=2, le=9, default=9)
    small_blind: int = Field(ge=1, default=10)
    big_blind: int = Field(ge=2, default=20)
    starting_chips: int = Field(ge=1, default=1000)
    allow_rebuy: bool = True
    rebuy_amount: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def validate_rules(self) -> "CreateGameRequest":
        if self.big_blind != 2 * self.small_blind:
            raise ValueError("big_blind must be exactly 2 * small_blind")
        if self.max_players < self.min_players:
            raise ValueError("max_players must be >= min_players")
        return self


class JoinGameRequest(BaseModel):
    player_name: str = Field(min_length=1, max_length=64)


class PlayerActionRequest(BaseModel):
    action: ActionType
    amount: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def validate_amount(self) -> "PlayerActionRequest":
        if self.action == ActionType.RAISE and self.amount is None:
            raise ValueError("amount is required for raise")
        if self.action in (ActionType.CHECK, ActionType.FOLD, ActionType.CALL, ActionType.ALL_IN):
            if self.amount is not None:
                raise ValueError(f"amount must be omitted for {self.action}")
        return self


# --- Response models ---

class CreateGameResponse(BaseModel):
    game_id: str
    banker_token: str
    banker_player_id: str


class JoinGameResponse(BaseModel):
    player_id: str
    player_token: str
    seat: int
    starting_chips: int


class CardModel(BaseModel):
    value: int
    rank: str
    suit: str
    display: str


class PlayerPublicView(BaseModel):
    player_id: str
    name: str
    seat: int
    chips: int
    role: str
    status: str
    bet_this_street: int
    is_current: bool


class SidePotView(BaseModel):
    level: int
    amount: int
    cap: int | None
    eligible_player_ids: list[str]


class TurnOptions(BaseModel):
    can_check: bool
    call_amount: int
    min_raise: int
    max_raise: int
    can_fold: bool


class GameStateResponse(BaseModel):
    game_id: str
    status: str
    street: str | None
    hand_number: int
    pot: int
    community_cards: list[CardModel]
    side_pots: list[SidePotView]
    players: list[PlayerPublicView]
    current_player_id: str | None
    dealer_seat: int | None
    small_blind: int
    big_blind: int
    min_players: int
    max_players: int
    allow_rebuy: bool
    current_turn_options: TurnOptions | None = None


class HandResponse(BaseModel):
    player_id: str
    hole_cards: list[CardModel]
    community_cards: list[CardModel]
    best_hand: list[CardModel] | None = None
    hand_description: str | None = None


class ActionResponse(BaseModel):
    success: bool
    action: str
    amount: int | None
    new_chips: int
    pot: int
    next_player_id: str | None
    street: str | None
    message: str


class RebuyResponse(BaseModel):
    success: bool
    new_chips: int
    amount_added: int


class PlayerListResponse(BaseModel):
    players: list[PlayerPublicView]
    total_chips_in_play: int


class StartGameResponse(BaseModel):
    success: bool
    game_state: GameStateResponse


class GameSummary(BaseModel):
    game_id: str
    status: str
    player_count: int
    max_players: int
    small_blind: int
    big_blind: int
    created_at: datetime


class LobbyResponse(BaseModel):
    games: list[GameSummary]


class SessionRecoveryResponse(BaseModel):
    player_id: str
    name: str
    game_id: str
    seat: int
    role: str
    status: str
    chips: int


class LeaveResponse(BaseModel):
    success: bool
    message: str


class SitOutResponse(BaseModel):
    success: bool


class SitInResponse(BaseModel):
    success: bool


class ActionHistoryItem(BaseModel):
    hand_number: int
    street: str
    player_id: str
    player_name: str
    action_type: str
    amount: int | None
    sequence: int
    created_at: datetime


class HandHistoryResponse(BaseModel):
    actions: list[ActionHistoryItem]
