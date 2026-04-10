<script setup lang="ts">
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api } from '../api'
import type { ActionHistoryItem, CardModel, GameStateResponse, HandResponse, HandResultItem } from '../api'
import { usePokerStore } from '../store'
import { pokerWs } from '../ws'
import type { WsEvent } from '../ws'
import PokerTable from '../components/PokerTable.vue'
import PlayerRow from '../components/PlayerRow.vue'
import CardSprite from '../components/CardSprite.vue'
import ActionPanel from '../components/ActionPanel.vue'
import BankerPanel from '../components/BankerPanel.vue'
import HandHistoryPanel from '../components/HandHistoryPanel.vue'

const route = useRoute()
const router = useRouter()
const store = usePokerStore()

const gameId = computed(() => route.params.id as string)
const gameState = computed(() => store.gameState)
const myHand = computed(() => store.myHand)

const history = ref<ActionHistoryItem[]>([])
const handResults = ref<HandResultItem[]>([])
const actionLoading = ref(false)
const actionError = ref<string | null>(null)
const timerSecs = ref<number | null>(null)
let timerInterval: ReturnType<typeof setInterval> | null = null
let pollInterval: ReturnType<typeof setInterval> | null = null

// Is this player the current active player?
const isMyTurn = computed(() => {
  if (!store.myPlayerId || !gameState.value) return false
  return gameState.value.current_player_id === store.myPlayerId
})

// Am I a banker for this game?
const isBanker = computed(() => store.myRole === 'banker' && store.gameId === gameId.value)

// My player record in the game
const me = computed(() =>
  gameState.value?.players.find((p) => p.player_id === store.myPlayerId) ?? null,
)

// Showdown reveal cards indexed by player_id
const showdownCards = ref<Record<string, CardModel[]>>({})

// ─── Session recovery ────────────────────────────────────────────────────────
async function recoverSession() {
  const token = store.myToken
  if (!token) return
  if (store.gameId !== gameId.value) {
    // Token belongs to a different game — clear it
    store.clearSession()
    return
  }
  try {
    const me = await api.getMe(token)
    if (me.game_id !== gameId.value) {
      store.clearSession()
    }
  } catch {
    store.clearSession()
  }
}

// ─── Load game state + history ───────────────────────────────────────────────
async function loadGame() {
  try {
    store.gameState = await api.getGame(gameId.value)
  } catch (e: unknown) {
    actionError.value = (e as Error).message
  }
}

async function loadHand() {
  if (!store.myToken) return
  try {
    store.myHand = await api.getHand(gameId.value, store.myToken)
  } catch {
    store.myHand = null
  }
}

async function loadHistory() {
  try {
    const res = await api.getHistory(gameId.value)
    history.value = res.actions
  } catch {
    // non-critical
  }
}

async function loadResults() {
  try {
    const res = await api.getResults(gameId.value)
    handResults.value = res.results
  } catch {
    // non-critical
  }
}

// ─── WebSocket ───────────────────────────────────────────────────────────────
function handleWsEvent(event: WsEvent) {
  switch (event.type) {
    case 'connected': {
      // Re-sync everything on (re)connect — covers tab waking from sleep / reconnect
      loadGame()
      loadHistory()
      loadResults()
      if (store.myToken && store.gameId === gameId.value) loadHand()
      break
    }
    case 'game_state': {
      store.gameState = event.data as GameStateResponse
      // Refresh hand so description + best_hand stay current as community cards change
      if (store.myToken && store.gameId === gameId.value) loadHand()
      break
    }
    case 'hole_cards': {
      const data = event.data as HandResponse
      if (data.player_id === store.myPlayerId) {
        store.myHand = data
      }
      break
    }
    case 'action': {
      loadHistory()
      loadResults()
      // Also refresh hand — best_hand description updates after each street
      if (store.myToken && store.gameId === gameId.value) loadHand()
      break
    }
    case 'showdown_reveal': {
      showdownCards.value = {}
      const entries = event.data as Array<{ player_id: string; hole_cards: CardModel[] }>
      for (const e of entries) {
        showdownCards.value[e.player_id] = e.hole_cards
      }
      // Refresh own hand for final description
      if (store.myToken && store.gameId === gameId.value) loadHand()
      break
    }
    case 'timer_sync': {
      const data = event.data as { player_id: string; expires_at: string }
      store.timerExpiresAt = data.expires_at
      startTimerCountdown(data.expires_at)
      break
    }
    case 'player_joined':
    case 'player_left': {
      loadGame()
      break
    }
  }
}

function startTimerCountdown(expiresAt: string) {
  if (timerInterval) clearInterval(timerInterval)
  const update = () => {
    const diff = Math.max(0, Math.round((new Date(expiresAt).getTime() - Date.now()) / 1000))
    timerSecs.value = diff
    if (diff === 0 && timerInterval) {
      clearInterval(timerInterval)
      timerInterval = null
      // Server will auto check/fold shortly; poll until game_state reflects new turn
      pollUntilTurnChanges(expiresAt)
    }
  }
  update()
  timerInterval = setInterval(update, 500)
}

// After timeout fires, poll every 2s for up to 15s until the game state changes
function pollUntilTurnChanges(timedOutAt: string) {
  const deadline = Date.now() + 15_000
  const previousPlayerId = store.gameState?.current_player_id
  const interval = setInterval(async () => {
    await Promise.all([loadGame(), loadHistory(), loadResults()])
    if (store.myToken && store.gameId === gameId.value) loadHand()
    const changed = store.gameState?.current_player_id !== previousPlayerId
      || store.gameState?.status !== 'running'
    if (changed || Date.now() > deadline) {
      clearInterval(interval)
      timerSecs.value = null
    }
  }, 2000)
}

let wsOff: (() => void) | null = null

// ─── Player actions ───────────────────────────────────────────────────────────
async function doAction(type: string, amount?: number) {
  if (!store.myToken) return
  actionLoading.value = true
  actionError.value = null
  try {
    await api.action(gameId.value, store.myToken, type, amount)
    // WS game_state/action events will arrive; also eagerly refresh so UI
    // is never stale if WS is slow or drops a frame
    await Promise.all([loadGame(), loadHand(), loadHistory(), loadResults()])
  } catch (e: unknown) {
    actionError.value = (e as Error).message
  } finally {
    actionLoading.value = false
  }
}

async function doStartGame() {
  if (!store.myToken) return
  actionLoading.value = true
  actionError.value = null
  try {
    await api.startGame(gameId.value, store.myToken)
    await Promise.all([loadGame(), loadHand(), loadHistory(), loadResults()])
  } catch (e: unknown) {
    actionError.value = (e as Error).message
  } finally {
    actionLoading.value = false
  }
}

async function doNextHand() {
  if (!store.myToken) return
  actionLoading.value = true
  actionError.value = null
  try {
    showdownCards.value = {}
    await api.nextHand(gameId.value, store.myToken)
    await Promise.all([loadGame(), loadHand(), loadHistory(), loadResults()])
  } catch (e: unknown) {
    actionError.value = (e as Error).message
  } finally {
    actionLoading.value = false
  }
}

async function doRebuy() {
  if (!store.myToken) return
  actionLoading.value = true
  actionError.value = null
  try {
    await api.rebuy(gameId.value, store.myToken)
    await loadGame()
  } catch (e: unknown) {
    actionError.value = (e as Error).message
  } finally {
    actionLoading.value = false
  }
}

async function doLeave() {
  if (!store.myToken) return
  if (!confirm('Leave the game?')) return
  try {
    await api.leaveGame(gameId.value, store.myToken)
  } catch {
    // ignore
  }
  store.clearSession()
  router.push('/')
}

async function doSitOut() {
  if (!store.myToken) return
  try {
    await api.sitOut(gameId.value, store.myToken)
    await loadGame()
  } catch (e: unknown) {
    actionError.value = (e as Error).message
  }
}

async function doSitIn() {
  if (!store.myToken) return
  try {
    await api.sitIn(gameId.value, store.myToken)
    await loadGame()
  } catch (e: unknown) {
    actionError.value = (e as Error).message
  }
}

// ─── Lifecycle ────────────────────────────────────────────────────────────────
onMounted(async () => {
  await recoverSession()
  await Promise.all([loadGame(), loadHistory(), loadResults()])
  if (store.myToken && store.gameId === gameId.value) {
    await loadHand()
  }

  pokerWs.connect(gameId.value, store.gameId === gameId.value ? store.myToken : null)
  wsOff = pokerWs.on(handleWsEvent)

  // Fallback poll: catches missed WS frames during street transitions / showdown
  pollInterval = setInterval(async () => {
    const status = store.gameState?.status
    if (status === 'running' || status === 'paused') {
      await loadGame()
      if (store.myToken && store.gameId === gameId.value) loadHand()
      if (status === 'paused') { loadHistory(); loadResults() }
    }
  }, 3000)
})

onUnmounted(() => {
  pokerWs.disconnect()
  if (wsOff) wsOff()
  if (timerInterval) clearInterval(timerInterval)
  if (pollInterval) clearInterval(pollInterval)
})
</script>

<template>
  <div class="min-h-screen bg-gray-900 text-white">
    <!-- Top bar -->
    <header class="bg-gray-800 border-b border-gray-700 px-4 py-2 flex items-center justify-between">
      <div class="flex items-center gap-4">
        <router-link to="/" class="text-gray-400 hover:text-white text-sm">← Lobby</router-link>
        <span class="text-yellow-300 font-bold">Texas Hold'em</span>
        <span v-if="gameState" class="text-xs text-gray-400 font-mono">
          Hand #{{ gameState.hand_number }} · {{ gameState.status }}
        </span>
      </div>
      <div class="flex items-center gap-2 text-sm">
        <span v-if="store.myName" class="text-gray-300">{{ store.myName }}</span>
        <span v-if="isBanker" class="text-xs bg-yellow-700 text-yellow-200 px-2 py-0.5 rounded">Banker</span>
        <button
          v-if="store.myToken && store.gameId === gameId"
          class="px-3 py-1 bg-gray-700 hover:bg-gray-600 rounded text-xs transition-colors"
          @click="doLeave"
        >Leave</button>
      </div>
    </header>

    <div v-if="!gameState" class="flex items-center justify-center h-64 text-gray-500">
      Loading…
    </div>

    <div v-else class="max-w-6xl mx-auto p-4 space-y-4">
      <!-- Error banner -->
      <div v-if="actionError" class="p-3 bg-red-900/60 rounded-lg text-red-300 text-sm flex items-center justify-between">
        <span>{{ actionError }}</span>
        <button class="ml-2 text-red-400 hover:text-red-200" @click="actionError = null">✕</button>
      </div>

      <!-- Turn timer -->
      <div
        v-if="timerSecs !== null && gameState.status === 'running'"
        class="flex items-center gap-2"
      >
        <div
          class="h-2 rounded-full flex-1 overflow-hidden bg-gray-700"
        >
          <div
            class="h-full transition-all duration-500"
            :class="timerSecs > 15 ? 'bg-green-500' : timerSecs > 5 ? 'bg-yellow-500' : 'bg-red-500'"
            :style="{ width: `${(timerSecs / 60) * 100}%` }"
          />
        </div>
        <span class="text-xs font-mono w-8 text-right" :class="timerSecs <= 5 ? 'text-red-400' : 'text-gray-400'">
          {{ timerSecs }}s
        </span>
      </div>

      <!-- Main grid -->
      <div class="grid grid-cols-1 lg:grid-cols-3 gap-4">
        <!-- Left: poker table -->
        <div class="lg:col-span-2 space-y-4">
          <PokerTable
            :community-cards="gameState.community_cards"
            :pot="gameState.pot"
            :side-pots="gameState.side_pots"
            :street="gameState.street"
          />

          <!-- My hole cards -->
          <div
            v-if="myHand && myHand.hole_cards.length > 0"
            class="bg-gray-800 rounded-xl p-4"
          >
            <h3 class="text-xs font-semibold text-gray-400 uppercase tracking-wide mb-3">Your Hand</h3>
            <div class="flex items-center gap-3 flex-wrap">
              <div class="flex gap-2">
                <CardSprite v-for="c in myHand.hole_cards" :key="c.value" :card="c" />
              </div>
              <div v-if="myHand.hand_description" class="text-sm text-yellow-300 font-semibold">
                {{ myHand.hand_description }}
              </div>
              <!-- Best hand -->
              <div v-if="myHand.best_hand" class="flex gap-1">
                <CardSprite v-for="c in myHand.best_hand" :key="c.value" :card="c" />
              </div>
            </div>
          </div>

          <!-- Players list -->
          <div class="space-y-2">
            <template v-for="p in gameState.players" :key="p.player_id">
              <PlayerRow
                :player="p"
                :is-me="p.player_id === store.myPlayerId"
                :dealer-seat="gameState.dealer_seat"
              >
                <!-- Showdown revealed cards -->
                <div v-if="showdownCards[p.player_id]" class="flex gap-1 px-3 pb-2">
                  <CardSprite
                    v-for="c in showdownCards[p.player_id]"
                    :key="c.value"
                    :card="c"
                  />
                </div>
              </PlayerRow>
            </template>
          </div>
        </div>

        <!-- Right: controls -->
        <div class="space-y-4">
          <!-- Action panel (my turn) -->
          <ActionPanel
            v-if="isMyTurn && gameState.current_turn_options"
            :options="gameState.current_turn_options"
            :my-chips="me?.chips ?? 0"
            :loading="actionLoading"
            @action="doAction"
          />

          <!-- Waiting for turn indicator -->
          <div
            v-else-if="store.myPlayerId && gameState.status === 'running' && !isMyTurn"
            class="bg-gray-800 rounded-xl p-4 text-sm text-gray-400"
          >
            Waiting for your turn…
          </div>

          <!-- Banker panel -->
          <BankerPanel
            v-if="isBanker"
            :status="gameState.status"
            :loading="actionLoading"
            :game-id="gameId"
            @start-game="doStartGame"
            @next-hand="doNextHand"
          />

          <!-- Sit out / sit in -->
          <div v-if="store.myToken && store.gameId === gameId && me" class="flex gap-2">
            <button
              v-if="me.status === 'active'"
              class="flex-1 px-3 py-1.5 bg-gray-700 hover:bg-gray-600 rounded-lg text-xs transition-colors"
              @click="doSitOut"
            >Sit Out</button>
            <button
              v-if="me.status === 'sitting_out'"
              class="flex-1 px-3 py-1.5 bg-blue-700 hover:bg-blue-600 rounded-lg text-xs transition-colors"
              @click="doSitIn"
            >Sit In</button>
          </div>

          <!-- Rebuy -->
          <div
            v-if="store.myToken && store.gameId === gameId && gameState.allow_rebuy && me && me.chips === 0 && me.status === 'eliminated'"
          >
            <button
              :disabled="actionLoading"
              class="w-full px-4 py-2 bg-green-700 hover:bg-green-600 rounded-lg font-semibold text-sm transition-colors disabled:opacity-50"
              @click="doRebuy"
            >Rebuy</button>
          </div>

          <!-- Observer note -->
          <div
            v-if="!store.myToken || store.gameId !== gameId"
            class="bg-gray-800 rounded-xl p-4 text-sm text-gray-400"
          >
            Spectating. <router-link to="/" class="text-blue-400 hover:text-blue-300 underline">Join from the lobby</router-link> to play.
          </div>

          <!-- Blinds info -->
          <div class="bg-gray-800 rounded-xl p-4 text-xs text-gray-400 space-y-1">
            <div>Blinds: {{ gameState.small_blind }} / {{ gameState.big_blind }}</div>
            <div>Players: {{ gameState.players.length }} / {{ gameState.max_players }}</div>
            <div v-if="gameState.allow_rebuy" class="text-green-500">Rebuys allowed</div>
          </div>
        </div>
      </div>

      <!-- Hand history -->
      <HandHistoryPanel :actions="history" :results="handResults" />
    </div>
  </div>
</template>
