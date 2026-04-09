// ─── Types ──────────────────────────────────────────────────────────────────

export interface CardModel {
  value: number
  rank: string
  suit: string
  display: string
}

export interface TurnOptions {
  can_check: boolean
  call_amount: number
  min_raise: number
  max_raise: number
  can_fold: boolean
}

export interface PlayerPublicView {
  player_id: string
  name: string
  seat: number
  chips: number
  role: string
  status: string
  bet_this_street: number
  is_current: boolean
}

export interface SidePotView {
  level: number
  amount: number
  cap: number | null
  eligible_player_ids: string[]
}

export interface GameStateResponse {
  game_id: string
  status: string
  street: string | null
  hand_number: number
  pot: number
  community_cards: CardModel[]
  side_pots: SidePotView[]
  players: PlayerPublicView[]
  current_player_id: string | null
  dealer_seat: number | null
  small_blind: number
  big_blind: number
  min_players: number
  max_players: number
  allow_rebuy: boolean
  current_turn_options: TurnOptions | null
}

export interface HandResponse {
  player_id: string
  hole_cards: CardModel[]
  community_cards: CardModel[]
  best_hand: CardModel[] | null
  hand_description: string | null
}

export interface GameSummary {
  game_id: string
  status: string
  player_count: number
  max_players: number
  small_blind: number
  big_blind: number
  created_at: string
}

export interface ActionHistoryItem {
  hand_number: number
  street: string
  player_id: string
  player_name: string
  action_type: string
  amount: number | null
  sequence: number
  created_at: string
}

export interface SessionRecoveryResponse {
  player_id: string
  name: string
  game_id: string
  seat: number
  role: string
  status: string
  chips: number
}

// ─── Helpers ────────────────────────────────────────────────────────────────

async function req<T>(path: string, options?: RequestInit): Promise<T> {
  const res = await fetch(path, options)
  if (!res.ok) {
    const body = await res.json().catch(() => ({ detail: res.statusText }))
    throw new Error(body.detail ?? res.statusText)
  }
  return res.json() as Promise<T>
}

function authHeaders(token: string) {
  return { 'X-Player-Token': token, 'Content-Type': 'application/json' }
}

// ─── API functions ───────────────────────────────────────────────────────────

export const api = {
  listGames: () => req<{ games: GameSummary[] }>('/games'),

  createGame: (body: {
    banker_name: string
    min_players?: number
    max_players?: number
    small_blind?: number
    big_blind?: number
    starting_chips?: number
    allow_rebuy?: boolean
    rebuy_amount?: number | null
  }) =>
    req<{ game_id: string; banker_token: string; banker_player_id: string }>('/games', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    }),

  joinGame: (gameId: string, playerName: string) =>
    req<{ player_id: string; player_token: string; seat: number; starting_chips: number }>(
      `/games/${gameId}/join`,
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ player_name: playerName }),
      },
    ),

  getGame: (gameId: string) => req<GameStateResponse>(`/games/${gameId}`),

  getPlayers: (gameId: string) =>
    req<{ players: PlayerPublicView[]; total_chips_in_play: number }>(`/games/${gameId}/players`),

  getHand: (gameId: string, token: string) =>
    req<HandResponse>(`/games/${gameId}/hand`, { headers: authHeaders(token) }),

  getHistory: (gameId: string) =>
    req<{ actions: ActionHistoryItem[] }>(`/games/${gameId}/history`),

  startGame: (gameId: string, token: string) =>
    req<{ success: boolean; game_state: GameStateResponse }>(`/games/${gameId}/start`, {
      method: 'POST',
      headers: authHeaders(token),
    }),

  nextHand: (gameId: string, token: string) =>
    req<{ success: boolean; game_state: GameStateResponse }>(`/games/${gameId}/next-hand`, {
      method: 'POST',
      headers: authHeaders(token),
    }),

  action: (
    gameId: string,
    token: string,
    action: string,
    amount?: number,
  ) =>
    req<{
      success: boolean
      action: string
      amount: number | null
      new_chips: number
      pot: number
      next_player_id: string | null
      street: string | null
      message: string
    }>(`/games/${gameId}/action`, {
      method: 'POST',
      headers: authHeaders(token),
      body: JSON.stringify(amount != null ? { action, amount } : { action }),
    }),

  rebuy: (gameId: string, token: string) =>
    req<{ success: boolean; new_chips: number; amount_added: number }>(`/games/${gameId}/rebuy`, {
      method: 'POST',
      headers: authHeaders(token),
    }),

  leaveGame: (gameId: string, token: string) =>
    req<{ success: boolean; message: string }>(`/games/${gameId}/leave`, {
      method: 'POST',
      headers: authHeaders(token),
    }),

  sitOut: (gameId: string, token: string) =>
    req<{ success: boolean }>(`/games/${gameId}/sit-out`, {
      method: 'POST',
      headers: authHeaders(token),
    }),

  sitIn: (gameId: string, token: string) =>
    req<{ success: boolean }>(`/games/${gameId}/sit-in`, {
      method: 'POST',
      headers: authHeaders(token),
    }),

  getMe: (token: string) =>
    req<SessionRecoveryResponse>('/me', { headers: { 'X-Player-Token': token } }),
}
