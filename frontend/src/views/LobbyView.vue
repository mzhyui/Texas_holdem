<script setup lang="ts">
import { ref, onMounted, onUnmounted } from 'vue'
import { useRouter } from 'vue-router'
import { api } from '../api'
import type { GameSummary } from '../api'
import { usePokerStore } from '../store'

const router = useRouter()
const store = usePokerStore()

const games = ref<GameSummary[]>([])
const loadError = ref<string | null>(null)

// Create game modal
const showCreate = ref(false)
const createForm = ref({
  banker_name: '',
  min_players: 2,
  max_players: 6,
  small_blind: 10,
  big_blind: 20,
  starting_chips: 1000,
  allow_rebuy: true,
  rebuy_amount: 500,
})
const createError = ref<string | null>(null)
const creating = ref(false)

// Join modal
const joinGameId = ref<string | null>(null)
const joinName = ref('')
const joinError = ref<string | null>(null)
const joining = ref(false)

let pollTimer: ReturnType<typeof setInterval> | null = null

async function fetchGames() {
  try {
    const res = await api.listGames()
    games.value = res.games
    loadError.value = null
  } catch (e: unknown) {
    loadError.value = (e as Error).message
  }
}

onMounted(() => {
  fetchGames()
  pollTimer = setInterval(fetchGames, 5000)
})

onUnmounted(() => {
  if (pollTimer) clearInterval(pollTimer)
})

async function createGame() {
  creating.value = true
  createError.value = null
  try {
    const body = {
      ...createForm.value,
      rebuy_amount: createForm.value.allow_rebuy ? createForm.value.rebuy_amount : undefined,
    }
    const res = await api.createGame(body)
    store.setSession({
      token: res.banker_token,
      playerId: res.banker_player_id,
      role: 'banker',
      name: createForm.value.banker_name,
      gId: res.game_id,
    })
    showCreate.value = false
    router.push(`/game/${res.game_id}`)
  } catch (e: unknown) {
    createError.value = (e as Error).message
  } finally {
    creating.value = false
  }
}

function openJoin(gameId: string) {
  joinGameId.value = gameId
  joinName.value = ''
  joinError.value = null
}

async function joinGame() {
  if (!joinGameId.value || !joinName.value.trim()) return
  joining.value = true
  joinError.value = null
  try {
    const res = await api.joinGame(joinGameId.value, joinName.value.trim())
    const gId = joinGameId.value
    store.setSession({
      token: res.player_token,
      playerId: res.player_id,
      role: 'player',
      name: joinName.value.trim(),
      gId,
    })
    joinGameId.value = null
    router.push(`/game/${gId}`)
  } catch (e: unknown) {
    joinError.value = (e as Error).message
  } finally {
    joining.value = false
  }
}

function spectate(gameId: string) {
  router.push(`/game/${gameId}`)
}

function syncBigBlind() {
  createForm.value.big_blind = createForm.value.small_blind * 2
}
</script>

<template>
  <div class="min-h-screen bg-gray-900 text-white p-6">
    <!-- Header -->
    <div class="max-w-4xl mx-auto">
      <div class="flex items-center justify-between mb-8">
        <h1 class="text-3xl font-bold text-yellow-300">Texas Hold'em</h1>
        <button
          class="px-4 py-2 bg-green-700 hover:bg-green-600 rounded-lg font-semibold text-sm transition-colors"
          @click="showCreate = true"
        >+ Create Game</button>
      </div>

      <!-- Error -->
      <div v-if="loadError" class="mb-4 p-3 bg-red-900/60 rounded-lg text-red-300 text-sm">
        {{ loadError }}
      </div>

      <!-- Games table -->
      <div class="bg-gray-800 rounded-xl overflow-hidden">
        <table class="w-full text-sm">
          <thead class="bg-gray-900 text-gray-400 text-xs uppercase">
            <tr>
              <th class="px-4 py-3 text-left">Game</th>
              <th class="px-4 py-3 text-left">Players</th>
              <th class="px-4 py-3 text-left">Blinds</th>
              <th class="px-4 py-3 text-left">Status</th>
              <th class="px-4 py-3" />
            </tr>
          </thead>
          <tbody>
            <tr
              v-for="g in games"
              :key="g.game_id"
              class="border-t border-gray-700 hover:bg-gray-700/40"
            >
              <td class="px-4 py-3 font-mono text-xs text-gray-400">{{ g.game_id.slice(0, 8) }}…</td>
              <td class="px-4 py-3">{{ g.player_count }} / {{ g.max_players }}</td>
              <td class="px-4 py-3 font-mono">{{ g.small_blind }}/{{ g.big_blind }}</td>
              <td class="px-4 py-3">
                <span
                  class="px-2 py-0.5 rounded text-xs font-semibold"
                  :class="{
                    'bg-green-800 text-green-200': g.status === 'waiting',
                    'bg-yellow-800 text-yellow-200': g.status === 'running',
                    'bg-blue-800 text-blue-200': g.status === 'paused',
                    'bg-gray-700 text-gray-400': g.status === 'finished',
                  }"
                >{{ g.status }}</span>
              </td>
              <td class="px-4 py-3 text-right space-x-2">
                <button
                  v-if="g.status === 'waiting'"
                  class="px-3 py-1 bg-blue-700 hover:bg-blue-600 rounded text-xs font-semibold transition-colors"
                  @click="openJoin(g.game_id)"
                >Join</button>
                <button
                  class="px-3 py-1 bg-gray-600 hover:bg-gray-500 rounded text-xs font-semibold transition-colors"
                  @click="spectate(g.game_id)"
                >Watch</button>
              </td>
            </tr>
            <tr v-if="games.length === 0">
              <td colspan="5" class="px-4 py-8 text-center text-gray-500">
                No games available. Create one!
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>

    <!-- Create Game Modal -->
    <Teleport to="body">
      <div
        v-if="showCreate"
        class="fixed inset-0 bg-black/70 flex items-center justify-center z-50 p-4"
        @click.self="showCreate = false"
      >
        <div class="bg-gray-800 rounded-2xl p-6 w-full max-w-md space-y-4">
          <h2 class="text-xl font-bold">Create Game</h2>

          <div v-if="createError" class="p-3 bg-red-900/60 rounded text-red-300 text-sm">
            {{ createError }}
          </div>

          <div class="space-y-3">
            <label class="block">
              <span class="text-xs text-gray-400">Your name</span>
              <input v-model="createForm.banker_name" class="input-field" placeholder="Alice" />
            </label>
            <div class="grid grid-cols-2 gap-3">
              <label class="block">
                <span class="text-xs text-gray-400">Min players</span>
                <input v-model.number="createForm.min_players" type="number" min="2" max="9" class="input-field" />
              </label>
              <label class="block">
                <span class="text-xs text-gray-400">Max players</span>
                <input v-model.number="createForm.max_players" type="number" min="2" max="9" class="input-field" />
              </label>
              <label class="block">
                <span class="text-xs text-gray-400">Small blind</span>
                <input v-model.number="createForm.small_blind" type="number" min="1" class="input-field" @input="syncBigBlind" />
              </label>
              <label class="block">
                <span class="text-xs text-gray-400">Big blind</span>
                <input v-model.number="createForm.big_blind" type="number" min="2" class="input-field" />
              </label>
              <label class="block col-span-2">
                <span class="text-xs text-gray-400">Starting chips</span>
                <input v-model.number="createForm.starting_chips" type="number" min="1" class="input-field" />
              </label>
            </div>
            <label class="flex items-center gap-2 text-sm">
              <input v-model="createForm.allow_rebuy" type="checkbox" class="accent-green-500" />
              Allow rebuy
            </label>
            <label v-if="createForm.allow_rebuy" class="block">
              <span class="text-xs text-gray-400">Rebuy amount</span>
              <input v-model.number="createForm.rebuy_amount" type="number" min="1" class="input-field" />
            </label>
          </div>

          <div class="flex gap-3 pt-2">
            <button
              :disabled="creating || !createForm.banker_name.trim()"
              class="flex-1 py-2 bg-green-700 hover:bg-green-600 rounded-lg font-semibold text-sm transition-colors disabled:opacity-50"
              @click="createGame"
            >{{ creating ? 'Creating…' : 'Create' }}</button>
            <button
              class="px-4 py-2 bg-gray-600 hover:bg-gray-500 rounded-lg text-sm transition-colors"
              @click="showCreate = false"
            >Cancel</button>
          </div>
        </div>
      </div>
    </Teleport>

    <!-- Join Modal -->
    <Teleport to="body">
      <div
        v-if="joinGameId"
        class="fixed inset-0 bg-black/70 flex items-center justify-center z-50 p-4"
        @click.self="joinGameId = null"
      >
        <div class="bg-gray-800 rounded-2xl p-6 w-full max-w-sm space-y-4">
          <h2 class="text-xl font-bold">Join Game</h2>
          <div v-if="joinError" class="p-3 bg-red-900/60 rounded text-red-300 text-sm">{{ joinError }}</div>
          <label class="block">
            <span class="text-xs text-gray-400">Your name</span>
            <input
              v-model="joinName"
              class="input-field"
              placeholder="Bob"
              @keyup.enter="joinGame"
            />
          </label>
          <div class="flex gap-3">
            <button
              :disabled="joining || !joinName.trim()"
              class="flex-1 py-2 bg-blue-700 hover:bg-blue-600 rounded-lg font-semibold text-sm transition-colors disabled:opacity-50"
              @click="joinGame"
            >{{ joining ? 'Joining…' : 'Join' }}</button>
            <button
              class="px-4 py-2 bg-gray-600 hover:bg-gray-500 rounded-lg text-sm transition-colors"
              @click="joinGameId = null"
            >Cancel</button>
          </div>
        </div>
      </div>
    </Teleport>
  </div>
</template>

<style scoped>
.input-field {
  @apply w-full mt-1 px-3 py-2 bg-gray-700 border border-gray-600 rounded-lg text-white text-sm focus:outline-none focus:border-blue-500;
}
</style>
