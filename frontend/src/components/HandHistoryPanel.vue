<script setup lang="ts">
import { computed, ref } from 'vue'
import type { ActionHistoryItem, HandResultItem } from '../api'
import CardSprite from './CardSprite.vue'

const props = defineProps<{
  actions: ActionHistoryItem[]
  results: HandResultItem[]
}>()

const open = ref(false)

const actionColor: Record<string, string> = {
  fold: 'text-gray-400',
  check: 'text-blue-300',
  call: 'text-blue-300',
  raise: 'text-yellow-300',
  all_in: 'text-orange-400',
  blind: 'text-gray-400',
  rebuy: 'text-green-300',
}

// Group actions by hand_number
const handNumbers = computed(() => {
  const nums = [...new Set(props.actions.map((a) => a.hand_number))]
  return nums.sort((a, b) => b - a) // descending (most recent first)
})

function actionsForHand(handNumber: number): ActionHistoryItem[] {
  return props.actions
    .filter((a) => a.hand_number === handNumber)
    .sort((a, b) => a.sequence - b.sequence)
}

function resultsForHand(handNumber: number): HandResultItem[] {
  return props.results.filter((r) => r.hand_number === handNumber)
}

function isWinner(result: HandResultItem): boolean {
  return result.pot_won > 0
}
</script>

<template>
  <div class="bg-gray-800 rounded-xl overflow-hidden">
    <button
      class="w-full flex items-center justify-between px-4 py-2 text-sm font-semibold text-gray-300 hover:bg-gray-700 transition-colors"
      @click="open = !open"
    >
      <span>Hand History ({{ actions.length }})</span>
      <span>{{ open ? '▲' : '▼' }}</span>
    </button>

    <div v-if="open" class="divide-y divide-gray-700">
      <div v-if="actions.length === 0" class="px-3 py-3 text-center text-gray-500 text-xs">
        No history yet
      </div>

      <!-- One section per hand, most recent on top -->
      <div v-for="handNum in handNumbers" :key="handNum" class="pb-2">
        <!-- Hand header -->
        <div class="px-3 py-1.5 bg-gray-900 text-xs font-bold text-gray-400 uppercase tracking-wide">
          Hand #{{ handNum }}
        </div>

        <!-- Round summary: one row per player result -->
        <div v-if="resultsForHand(handNum).length > 0" class="px-3 pt-2 pb-1 space-y-1.5">
          <div
            v-for="result in resultsForHand(handNum)"
            :key="result.player_id"
            class="flex items-center gap-3 rounded-lg px-2 py-1.5 text-xs"
            :class="isWinner(result) ? 'bg-yellow-900/40 border border-yellow-700/50' : 'bg-gray-700/30'"
          >
            <!-- Winner crown -->
            <span v-if="isWinner(result)" class="text-yellow-400 text-sm shrink-0">★</span>
            <span v-else class="w-4 shrink-0" />

            <!-- Player name -->
            <span
              class="font-semibold w-24 truncate shrink-0"
              :class="isWinner(result) ? 'text-yellow-300' : 'text-gray-300'"
            >{{ result.player_name }}</span>

            <!-- Hand description -->
            <span class="text-gray-400 flex-1 truncate">{{ result.hand_description ?? '—' }}</span>

            <!-- Hole cards -->
            <div class="flex gap-0.5 shrink-0">
              <div
                v-for="c in result.hole_cards"
                :key="c.value"
                class="w-7 h-9 rounded text-center flex flex-col items-center justify-center shadow text-xs font-bold select-none"
                :class="(c.suit === 'h' || c.suit === 'd') ? 'bg-white text-red-600 border border-gray-300' : 'bg-white text-gray-900 border border-gray-300'"
              >
                <span class="leading-none">{{ c.rank }}</span>
                <span class="leading-none">{{ c.suit === 'h' ? '♥' : c.suit === 'd' ? '♦' : c.suit === 's' ? '♠' : '♣' }}</span>
              </div>
            </div>

            <!-- Chip change -->
            <span
              class="font-mono w-20 text-right shrink-0 font-semibold"
              :class="isWinner(result) ? 'text-green-400' : 'text-gray-500'"
            >
              {{ isWinner(result) ? '+' + result.pot_won.toLocaleString() : '' }}
            </span>
          </div>
        </div>

        <!-- Action rows for this hand -->
        <table class="w-full text-xs text-left mt-1">
          <thead class="bg-gray-900 text-gray-500">
            <tr>
              <th class="px-3 py-1">Street</th>
              <th class="px-3 py-1">Player</th>
              <th class="px-3 py-1">Action</th>
              <th class="px-3 py-1 text-right">Amount</th>
            </tr>
          </thead>
          <tbody>
            <tr
              v-for="row in actionsForHand(handNum)"
              :key="row.sequence + '-' + row.hand_number"
              class="border-t border-gray-700/50 hover:bg-gray-700/40"
            >
              <td class="px-3 py-1 text-gray-400">{{ row.street }}</td>
              <td class="px-3 py-1 font-semibold text-gray-200">{{ row.player_name }}</td>
              <td class="px-3 py-1" :class="actionColor[row.action_type] ?? 'text-white'">
                {{ row.action_type }}
              </td>
              <td class="px-3 py-1 text-right font-mono text-gray-300">
                {{ row.amount != null ? row.amount.toLocaleString() : '—' }}
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>
  </div>
</template>
