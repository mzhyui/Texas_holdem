import enum
import json
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class GameStatus(str, enum.Enum):
    WAITING = "waiting"
    RUNNING = "running"
    PAUSED = "paused"
    FINISHED = "finished"


class Street(str, enum.Enum):
    PRE_FLOP = "pre_flop"
    FLOP = "flop"
    TURN = "turn"
    RIVER = "river"
    SHOWDOWN = "showdown"


class PlayerStatus(str, enum.Enum):
    ACTIVE = "active"
    FOLDED = "folded"
    ALL_IN = "all_in"
    SITTING_OUT = "sitting_out"
    ELIMINATED = "eliminated"


class ActionType(str, enum.Enum):
    CHECK = "check"
    CALL = "call"
    RAISE = "raise"
    FOLD = "fold"
    ALL_IN = "all_in"
    REBUY = "rebuy"
    BLIND = "blind"


class Game(Base):
    __tablename__ = "games"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default=GameStatus.WAITING)
    min_players: Mapped[int] = mapped_column(Integer, nullable=False, default=2)
    max_players: Mapped[int] = mapped_column(Integer, nullable=False, default=9)
    small_blind: Mapped[int] = mapped_column(Integer, nullable=False)
    big_blind: Mapped[int] = mapped_column(Integer, nullable=False)
    allow_rebuy: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    rebuy_amount: Mapped[int | None] = mapped_column(Integer, nullable=True)
    starting_chips: Mapped[int] = mapped_column(Integer, nullable=False)
    dealer_seat: Mapped[int | None] = mapped_column(Integer, nullable=True)
    current_street: Mapped[str | None] = mapped_column(String(16), nullable=True)
    pot: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # JSON-encoded list[int]
    _community_cards: Mapped[str] = mapped_column("community_cards", Text, nullable=False, default="[]")
    # JSON-encoded list[int] (remaining deck)
    _deck_state: Mapped[str] = mapped_column("deck_state", Text, nullable=False, default="[]")
    current_player_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("players.id", use_alter=True, name="fk_game_current_player"), nullable=True
    )
    aggressor_player_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("players.id", use_alter=True, name="fk_game_aggressor"), nullable=True
    )
    hand_number: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_raise_size: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # JSON-encoded set of player IDs who have acted this street (for round-close detection)
    _players_acted: Mapped[str] = mapped_column("players_acted", Text, nullable=False, default="[]")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())

    players: Mapped[list["Player"]] = relationship(
        "Player",
        back_populates="game",
        foreign_keys="Player.game_id",
        cascade="all, delete-orphan",
    )
    actions: Mapped[list["Action"]] = relationship(
        "Action", back_populates="game", cascade="all, delete-orphan"
    )
    side_pots: Mapped[list["SidePot"]] = relationship(
        "SidePot", back_populates="game", cascade="all, delete-orphan"
    )
    hand_results: Mapped[list["HandResult"]] = relationship(
        "HandResult", back_populates="game", cascade="all, delete-orphan"
    )

    @property
    def community_cards(self) -> list[int]:
        return json.loads(self._community_cards)

    @community_cards.setter
    def community_cards(self, value: list[int]) -> None:
        self._community_cards = json.dumps(value)

    @property
    def deck_state(self) -> list[int]:
        return json.loads(self._deck_state)

    @deck_state.setter
    def deck_state(self, value: list[int]) -> None:
        self._deck_state = json.dumps(value)

    @property
    def players_acted(self) -> list[str]:
        return json.loads(self._players_acted)

    @players_acted.setter
    def players_acted(self, value: list[str]) -> None:
        self._players_acted = json.dumps(value)


class Player(Base):
    __tablename__ = "players"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    game_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("games.id", ondelete="CASCADE"), nullable=False
    )
    token: Mapped[str] = mapped_column(String(36), unique=True, nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    role: Mapped[str] = mapped_column(String(16), nullable=False, default="player")
    seat: Mapped[int] = mapped_column(Integer, nullable=False)
    chips: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default=PlayerStatus.ACTIVE)
    bet_this_street: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_bet_this_hand: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    _hole_cards: Mapped[str | None] = mapped_column("hole_cards", Text, nullable=True)
    joined_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    game: Mapped["Game"] = relationship("Game", back_populates="players", foreign_keys=[game_id])
    actions: Mapped[list["Action"]] = relationship("Action", back_populates="player")
    hand_results: Mapped[list["HandResult"]] = relationship("HandResult", back_populates="player")

    @property
    def hole_cards(self) -> list[int]:
        if self._hole_cards is None:
            return []
        return json.loads(self._hole_cards)

    @hole_cards.setter
    def hole_cards(self, value: list[int] | None) -> None:
        self._hole_cards = json.dumps(value) if value is not None else None


class Action(Base):
    __tablename__ = "actions"
    __table_args__ = (
        Index("ix_actions_game_hand_seq", "game_id", "hand_number", "sequence"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    game_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("games.id", ondelete="CASCADE"), nullable=False
    )
    player_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("players.id", ondelete="CASCADE"), nullable=False
    )
    hand_number: Mapped[int] = mapped_column(Integer, nullable=False)
    street: Mapped[str] = mapped_column(String(16), nullable=False)
    action_type: Mapped[str] = mapped_column(String(16), nullable=False)
    amount: Mapped[int | None] = mapped_column(Integer, nullable=True)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    game: Mapped["Game"] = relationship("Game", back_populates="actions")
    player: Mapped["Player"] = relationship("Player", back_populates="actions")


class SidePot(Base):
    __tablename__ = "side_pots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    game_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("games.id", ondelete="CASCADE"), nullable=False
    )
    hand_number: Mapped[int] = mapped_column(Integer, nullable=False)
    level: Mapped[int] = mapped_column(Integer, nullable=False)
    amount: Mapped[int] = mapped_column(Integer, nullable=False)
    cap: Mapped[int | None] = mapped_column(Integer, nullable=True)
    _eligible_player_ids: Mapped[str] = mapped_column("eligible_player_ids", Text, nullable=False)

    game: Mapped["Game"] = relationship("Game", back_populates="side_pots")

    @property
    def eligible_player_ids(self) -> list[str]:
        return json.loads(self._eligible_player_ids)

    @eligible_player_ids.setter
    def eligible_player_ids(self, value: list[str]) -> None:
        self._eligible_player_ids = json.dumps(value)


class HandResult(Base):
    __tablename__ = "hand_results"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    game_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("games.id", ondelete="CASCADE"), nullable=False
    )
    hand_number: Mapped[int] = mapped_column(Integer, nullable=False)
    player_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("players.id", ondelete="CASCADE"), nullable=False
    )
    _hole_cards: Mapped[str] = mapped_column("hole_cards", Text, nullable=False)
    _best_hand: Mapped[str | None] = mapped_column("best_hand", Text, nullable=True)
    hand_rank: Mapped[int | None] = mapped_column(Integer, nullable=True)
    hand_description: Mapped[str | None] = mapped_column(String(128), nullable=True)
    pot_won: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    game: Mapped["Game"] = relationship("Game", back_populates="hand_results")
    player: Mapped["Player"] = relationship("Player", back_populates="hand_results")

    @property
    def hole_cards(self) -> list[int]:
        return json.loads(self._hole_cards)

    @hole_cards.setter
    def hole_cards(self, value: list[int]) -> None:
        self._hole_cards = json.dumps(value)

    @property
    def best_hand(self) -> list[int] | None:
        if self._best_hand is None:
            return None
        return json.loads(self._best_hand)

    @best_hand.setter
    def best_hand(self, value: list[int] | None) -> None:
        self._best_hand = json.dumps(value) if value is not None else None
