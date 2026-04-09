<script setup lang="ts">
import { ref } from 'vue'
import type { ActionHistoryItem } from '../api'

const props = defineProps<{ actions: ActionHistoryItem[] }>()
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

    <div v-if="open" class="overflow-x-auto">
      <table class="w-full text-xs text-left">
        <thead class="bg-gray-900 text-gray-500">
          <tr>
            <th class="px-3 py-1.5">Hand</th>
            <th class="px-3 py-1.5">Street</th>
            <th class="px-3 py-1.5">Player</th>
            <th class="px-3 py-1.5">Action</th>
            <th class="px-3 py-1.5 text-right">Amount</th>
          </tr>
        </thead>
        <tbody>
          <tr
            v-for="row in actions"
            :key="row.sequence + '-' + row.hand_number"
            class="border-t border-gray-700 hover:bg-gray-700/40"
          >
            <td class="px-3 py-1 font-mono text-gray-500">#{{ row.hand_number }}</td>
            <td class="px-3 py-1 text-gray-400">{{ row.street }}</td>
            <td class="px-3 py-1 font-semibold">{{ row.player_name }}</td>
            <td class="px-3 py-1" :class="actionColor[row.action_type] ?? 'text-white'">
              {{ row.action_type }}
            </td>
            <td class="px-3 py-1 text-right font-mono text-gray-300">
              {{ row.amount != null ? row.amount.toLocaleString() : '—' }}
            </td>
          </tr>
          <tr v-if="actions.length === 0">
            <td colspan="5" class="px-3 py-3 text-center text-gray-500">No history yet</td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</template>
