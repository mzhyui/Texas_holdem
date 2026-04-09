<script setup lang="ts">
import { ref, computed } from 'vue'
import type { TurnOptions } from '../api'

const props = defineProps<{
  options: TurnOptions
  myChips: number
  loading: boolean
}>()

const emit = defineEmits<{
  action: [type: string, amount?: number]
}>()

const raiseAmount = ref(props.options.min_raise)

const clampedRaise = computed(() =>
  Math.max(props.options.min_raise, Math.min(raiseAmount.value, props.options.max_raise)),
)

function send(type: string, amount?: number) {
  emit('action', type, amount)
}
</script>

<template>
  <div class="bg-gray-800 rounded-xl p-4 space-y-3">
    <h3 class="text-sm font-semibold text-gray-400 uppercase tracking-wide">Your turn</h3>

    <div class="flex flex-wrap gap-2">
      <!-- Check -->
      <button
        v-if="options.can_check"
        :disabled="loading"
        class="btn-action bg-blue-700 hover:bg-blue-600"
        @click="send('check')"
      >Check</button>

      <!-- Call -->
      <button
        v-if="!options.can_check && options.call_amount > 0"
        :disabled="loading"
        class="btn-action bg-blue-700 hover:bg-blue-600"
        @click="send('call')"
      >Call {{ options.call_amount.toLocaleString() }}</button>

      <!-- All-in -->
      <button
        :disabled="loading"
        class="btn-action bg-orange-700 hover:bg-orange-600"
        @click="send('all_in')"
      >All-in</button>

      <!-- Fold -->
      <button
        v-if="options.can_fold"
        :disabled="loading"
        class="btn-action bg-gray-600 hover:bg-gray-500"
        @click="send('fold')"
      >Fold</button>
    </div>

    <!-- Raise slider -->
    <div v-if="options.max_raise > options.min_raise" class="space-y-1">
      <div class="flex justify-between text-xs text-gray-400">
        <span>Raise</span>
        <span class="font-mono text-white">{{ clampedRaise.toLocaleString() }}</span>
      </div>
      <input
        v-model.number="raiseAmount"
        type="range"
        :min="options.min_raise"
        :max="options.max_raise"
        :step="1"
        class="w-full accent-yellow-400"
      />
      <div class="flex justify-between text-xs text-gray-500">
        <span>min {{ options.min_raise.toLocaleString() }}</span>
        <span>max {{ options.max_raise.toLocaleString() }}</span>
      </div>
      <button
        :disabled="loading"
        class="btn-action w-full bg-yellow-700 hover:bg-yellow-600"
        @click="send('raise', clampedRaise)"
      >Raise to {{ clampedRaise.toLocaleString() }}</button>
    </div>
    <div v-else-if="options.min_raise <= options.max_raise" class="pt-1">
      <button
        :disabled="loading"
        class="btn-action w-full bg-yellow-700 hover:bg-yellow-600"
        @click="send('raise', options.min_raise)"
      >Raise {{ options.min_raise.toLocaleString() }}</button>
    </div>
  </div>
</template>

<style scoped>
.btn-action {
  @apply px-4 py-2 rounded-lg text-sm font-semibold text-white transition-colors disabled:opacity-50 disabled:cursor-not-allowed;
}
</style>
