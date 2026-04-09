import { defineStore } from 'pinia'
import { ref } from 'vue'
import type { GameStateResponse, HandResponse } from './api'

export const usePokerStore = defineStore('poker', () => {
  const myToken = ref<string | null>(localStorage.getItem('poker_token'))
  const myPlayerId = ref<string | null>(localStorage.getItem('poker_player_id'))
  const myRole = ref<string | null>(localStorage.getItem('poker_role'))
  const myName = ref<string | null>(localStorage.getItem('poker_name'))
  const gameId = ref<string | null>(localStorage.getItem('poker_game_id'))

  const gameState = ref<GameStateResponse | null>(null)
  const myHand = ref<HandResponse | null>(null)
  const timerExpiresAt = ref<string | null>(null)
  const error = ref<string | null>(null)

  function setSession(opts: {
    token: string
    playerId: string
    role: string
    name: string
    gId: string
  }) {
    myToken.value = opts.token
    myPlayerId.value = opts.playerId
    myRole.value = opts.role
    myName.value = opts.name
    gameId.value = opts.gId
    localStorage.setItem('poker_token', opts.token)
    localStorage.setItem('poker_player_id', opts.playerId)
    localStorage.setItem('poker_role', opts.role)
    localStorage.setItem('poker_name', opts.name)
    localStorage.setItem('poker_game_id', opts.gId)
  }

  function clearSession() {
    myToken.value = null
    myPlayerId.value = null
    myRole.value = null
    myName.value = null
    gameId.value = null
    localStorage.removeItem('poker_token')
    localStorage.removeItem('poker_player_id')
    localStorage.removeItem('poker_role')
    localStorage.removeItem('poker_name')
    localStorage.removeItem('poker_game_id')
    gameState.value = null
    myHand.value = null
    timerExpiresAt.value = null
  }

  return {
    myToken,
    myPlayerId,
    myRole,
    myName,
    gameId,
    gameState,
    myHand,
    timerExpiresAt,
    error,
    setSession,
    clearSession,
  }
})
