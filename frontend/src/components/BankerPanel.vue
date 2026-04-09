<script setup lang="ts">
const props = defineProps<{
  status: string
  loading: boolean
}>()

const emit = defineEmits<{
  startGame: []
  nextHand: []
}>()
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
  </div>
</template>
