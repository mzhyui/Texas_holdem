<script setup lang="ts">
import type { CardModel } from '../api'

const props = defineProps<{ card: CardModel; faceDown?: boolean }>()

const suitColor: Record<string, string> = {
  h: 'text-red-500',
  d: 'text-red-500',
  s: 'text-gray-900',
  c: 'text-gray-900',
}

const suitSymbol: Record<string, string> = {
  h: '♥',
  d: '♦',
  s: '♠',
  c: '♣',
}
</script>

<template>
  <div
    v-if="faceDown"
    class="w-12 h-16 rounded-md bg-blue-800 border-2 border-blue-600 flex items-center justify-center shadow-md select-none"
  >
    <span class="text-blue-400 text-2xl">🂠</span>
  </div>
  <div
    v-else
    class="w-12 h-16 rounded-md bg-white border border-gray-300 flex flex-col items-center justify-center shadow-md select-none"
  >
    <span class="text-xs font-bold leading-none" :class="suitColor[card.suit]">{{ card.rank }}</span>
    <span class="text-lg leading-none" :class="suitColor[card.suit]">{{ suitSymbol[card.suit] }}</span>
  </div>
</template>
