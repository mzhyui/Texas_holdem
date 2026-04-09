import type { CardModel, GameStateResponse, HandResponse } from './api'

export type WsEventType =
  | 'game_state'
  | 'action'
  | 'hole_cards'
  | 'player_joined'
  | 'player_left'
  | 'showdown_reveal'
  | 'timer_sync'

export interface WsEvent {
  type: WsEventType
  data: unknown
}

export interface ShowdownEntry {
  player_id: string
  hole_cards: CardModel[]
}

type Listener = (event: WsEvent) => void

class PokerWebSocket {
  private ws: WebSocket | null = null
  private listeners: Listener[] = []
  private gameId: string | null = null
  private token: string | null = null
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null

  connect(gameId: string, token: string | null) {
    this.disconnect()
    this.gameId = gameId
    this.token = token

    const proto = location.protocol === 'https:' ? 'wss' : 'ws'
    const url = token
      ? `${proto}://${location.host}/games/${gameId}/ws?token=${token}`
      : `${proto}://${location.host}/games/${gameId}/ws`

    this.ws = new WebSocket(url)

    this.ws.onmessage = (e) => {
      try {
        const event = JSON.parse(e.data) as WsEvent
        this.listeners.forEach((l) => l(event))
      } catch {
        // ignore malformed frames
      }
    }

    this.ws.onclose = (e) => {
      // 4001 = invalid token, don't reconnect
      if (e.code === 4001) return
      this.reconnectTimer = setTimeout(() => this.connect(this.gameId!, this.token), 3000)
    }
  }

  disconnect() {
    if (this.reconnectTimer) {
      clearTimeout(this.reconnectTimer)
      this.reconnectTimer = null
    }
    if (this.ws) {
      this.ws.onclose = null
      this.ws.close()
      this.ws = null
    }
  }

  on(listener: Listener) {
    this.listeners.push(listener)
    return () => {
      this.listeners = this.listeners.filter((l) => l !== listener)
    }
  }
}

export const pokerWs = new PokerWebSocket()
