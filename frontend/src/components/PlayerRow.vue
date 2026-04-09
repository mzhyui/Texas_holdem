<script setup lang="ts">
import type { PlayerPublicView } from '../api'

const props = defineProps<{
  player: PlayerPublicView
  isMe: boolean
  dealerSeat: number | null
}>()

const statusBadge: Record<string, string> = {
  active: 'bg-green-700 text-green-100',
  folded: 'bg-gray-600 text-gray-300',
  all_in: 'bg-yellow-700 text-yellow-100',
  sitting_out: 'bg-gray-700 text-gray-400',
  eliminated: 'bg-red-900 text-red-300',
}
</script>

<template>
  <div
    class="flex items-center gap-3 px-3 py-2 rounded-lg"
    :class="[
      player.is_current ? 'bg-yellow-900/60 ring-2 ring-yellow-400' : 'bg-gray-800',
      isMe ? 'ring-2 ring-blue-500' : '',
    ]"
  >
    <!-- Seat / dealer badge -->
    <div class="w-8 h-8 flex items-center justify-center rounded-full bg-gray-700 text-xs font-bold shrink-0">
      <span v-if="dealerSeat === player.seat" title="Dealer">D</span>
      <span v-else>{{ player.seat }}</span>
    </div>

    <!-- Name + status -->
    <div class="flex-1 min-w-0">
      <div class="flex items-center gap-1">
        <span class="font-semibold truncate">{{ player.name }}</span>
        <span v-if="isMe" class="text-xs text-blue-400">(you)</span>
      </div>
      <span
        class="text-xs px-1.5 py-0.5 rounded"
        :class="statusBadge[player.status] ?? 'bg-gray-600 text-gray-300'"
      >{{ player.status }}</span>
    </div>

    <!-- Chips + bet -->
    <div class="text-right shrink-0">
      <div class="font-mono font-bold text-green-300">{{ player.chips.toLocaleString() }}</div>
      <div v-if="player.bet_this_street > 0" class="text-xs text-yellow-300">
        bet {{ player.bet_this_street.toLocaleString() }}
      </div>
    </div>
  </div>
  <slot />
</template>
