<script setup lang="ts">
import { ref, onMounted, watch } from 'vue'
import { api } from '../api'
import type { BotInfo, AddBotRequest } from '../api'

const props = defineProps<{
  status: string
  loading: boolean
  gameId: string
}>()

const emit = defineEmits<{
  startGame: []
  nextHand: []
}>()

// ── Bot state ──────────────────────────────────────────────────────────────
const bots = ref<BotInfo[]>([])
const botLoading = ref(false)
const botError = ref<string | null>(null)
const showAddForm = ref(false)

const form = ref<AddBotRequest>({
  name: 'PokerBot',
  style: 'mild',
  llm_endpoint: '',
  llm_api_key: '',
  llm_model: 'gpt-4o-mini',
})

async function loadBots() {
  try {
    const res = await api.listBots(props.gameId)
    bots.value = res.bots
  } catch {
    // non-critical
  }
}

async function addBot() {
  botLoading.value = true
  botError.value = null
  try {
    await api.addBot(props.gameId, { ...form.value })
    showAddForm.value = false
    await loadBots()
  } catch (e: unknown) {
    botError.value = (e as Error).message
  } finally {
    botLoading.value = false
  }
}

async function kickBot(botId: string) {
  botLoading.value = true
  botError.value = null
  try {
    await api.kickBot(props.gameId, botId)
    await loadBots()
  } catch (e: unknown) {
    botError.value = (e as Error).message
  } finally {
    botLoading.value = false
  }
}

onMounted(loadBots)
// Refresh bot list when game status changes (e.g. after next hand)
watch(() => props.status, loadBots)
</script>

<template>
  <div class="bg-gray-800 rounded-xl p-4 space-y-2">
    <h3 class="text-sm font-semibold text-gray-400 uppercase tracking-wide">Banker</h3>

    <button
      v-if="status === 'waiting'"
      :disabled="loading"
      class="w-full px-4 py-2 rounded-lg bg-green-700 hover:bg-green-600 text-white font-semibold text-sm transition-colors disabled:opacity-50"
      @click="emit('startGame')"
    >Start Game</button>

    <button
      v-if="status === 'paused'"
      :disabled="loading"
      class="w-full px-4 py-2 rounded-lg bg-green-700 hover:bg-green-600 text-white font-semibold text-sm transition-colors disabled:opacity-50"
      @click="emit('nextHand')"
    >Next Hand</button>

    <p v-if="status === 'running'" class="text-xs text-gray-500">Hand in progress</p>
    <p v-if="status === 'finished'" class="text-xs text-gray-400">Game finished</p>

    <!-- ── Bot management ─────────────────────────────────────────────── -->
    <div class="border-t border-gray-700 pt-3 mt-1 space-y-2">
      <div class="flex items-center justify-between">
        <span class="text-xs font-semibold text-gray-400 uppercase tracking-wide">Bots</span>
        <button
          class="text-xs px-2 py-0.5 rounded bg-blue-700 hover:bg-blue-600 text-white transition-colors"
          @click="showAddForm = !showAddForm"
        >{{ showAddForm ? 'Cancel' : '+ Add Bot' }}</button>
      </div>

      <!-- Error -->
      <div v-if="botError" class="text-xs text-red-400 bg-red-900/30 rounded px-2 py-1 flex justify-between">
        <span>{{ botError }}</span>
        <button class="ml-1 text-red-400 hover:text-red-200" @click="botError = null">✕</button>
      </div>

      <!-- Add bot form -->
      <div v-if="showAddForm" class="space-y-2 bg-gray-700/50 rounded-lg p-3 text-xs">
        <div class="space-y-1">
          <label class="text-gray-400">Name</label>
          <input
            v-model="form.name"
            class="w-full bg-gray-900 border border-gray-600 rounded px-2 py-1 text-white focus:outline-none focus:border-blue-500"
            placeholder="PokerBot"
          />
        </div>

        <div class="space-y-1">
          <label class="text-gray-400">Style</label>
          <select
            v-model="form.style"
            class="w-full bg-gray-900 border border-gray-600 rounded px-2 py-1 text-white focus:outline-none focus:border-blue-500"
          >
            <option value="aggressive">Aggressive</option>
            <option value="mild">Mild</option>
            <option value="passive">Passive</option>
          </select>
        </div>

        <div class="border-t border-gray-600 pt-2 space-y-1">
          <p class="text-gray-500 text-xs">LLM (optional — leave blank for heuristic only)</p>
          <label class="text-gray-400">API Endpoint</label>
          <input
            v-model="form.llm_endpoint"
            class="w-full bg-gray-900 border border-gray-600 rounded px-2 py-1 text-white focus:outline-none focus:border-blue-500"
            placeholder="https://api.openai.com/v1"
          />
        </div>

        <div class="space-y-1">
          <label class="text-gray-400">API Key</label>
          <input
            v-model="form.llm_api_key"
            type="password"
            class="w-full bg-gray-900 border border-gray-600 rounded px-2 py-1 text-white focus:outline-none focus:border-blue-500"
            placeholder="sk-..."
          />
        </div>

        <div class="space-y-1">
          <label class="text-gray-400">Model</label>
          <input
            v-model="form.llm_model"
            class="w-full bg-gray-900 border border-gray-600 rounded px-2 py-1 text-white focus:outline-none focus:border-blue-500"
            placeholder="gpt-4o-mini"
          />
        </div>

        <button
          :disabled="botLoading"
          class="w-full px-3 py-1.5 rounded bg-blue-700 hover:bg-blue-600 text-white font-semibold transition-colors disabled:opacity-50"
          @click="addBot"
        >{{ botLoading ? 'Adding…' : 'Add Bot' }}</button>
      </div>

      <!-- Active bots list -->
      <div v-if="bots.length > 0" class="space-y-1">
        <div
          v-for="bot in bots"
          :key="bot.bot_id"
          class="flex items-center justify-between bg-gray-700/40 rounded px-2 py-1.5 text-xs"
        >
          <div class="flex items-center gap-2 min-w-0">
            <span class="font-semibold text-gray-200 truncate">{{ bot.name }}</span>
            <span class="text-gray-500 shrink-0">{{ bot.style }}</span>
            <span v-if="bot.llm_enabled" class="text-blue-400 shrink-0">LLM</span>
          </div>
          <button
            :disabled="botLoading"
            class="ml-2 px-2 py-0.5 rounded bg-red-800 hover:bg-red-700 text-red-200 transition-colors disabled:opacity-50 shrink-0"
            @click="kickBot(bot.bot_id)"
          >Kick</button>
        </div>
      </div>
      <p v-else class="text-xs text-gray-600">No bots running</p>
    </div>
  </div>
</template>
