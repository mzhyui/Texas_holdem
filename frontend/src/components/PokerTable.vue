<script setup lang="ts">
import CardSprite from './CardSprite.vue'
import type { CardModel, SidePotView } from '../api'

const props = defineProps<{
  communityCards: CardModel[]
  pot: number
  sidePots: SidePotView[]
  street: string | null
}>()
</script>

<template>
  <div class="bg-felt rounded-2xl p-6 space-y-4 shadow-inner">
    <!-- Street label -->
    <div class="text-center">
      <span class="text-xs uppercase tracking-widest text-felt-light font-semibold">
        {{ street ?? 'Waiting' }}
      </span>
    </div>

    <!-- Community cards -->
    <div class="flex gap-2 justify-center min-h-[4rem]">
      <CardSprite
        v-for="(card, i) in communityCards"
        :key="i"
        :card="card"
      />
      <!-- placeholder slots -->
      <div
        v-for="n in Math.max(0, 5 - communityCards.length)"
        :key="'ph' + n"
        class="w-12 h-16 rounded-md border-2 border-dashed border-felt-light/30"
      />
    </div>

    <!-- Pot -->
    <div class="text-center">
      <span class="text-yellow-300 font-bold text-lg">
        Pot: {{ pot.toLocaleString() }}
      </span>
    </div>

    <!-- Side pots -->
    <div v-if="sidePots.length > 1" class="flex flex-wrap gap-2 justify-center">
      <span
        v-for="sp in sidePots"
        :key="sp.level"
        class="text-xs bg-felt-dark/60 px-2 py-0.5 rounded text-yellow-200"
      >
        Pot {{ sp.level }}: {{ sp.amount.toLocaleString() }}
      </span>
    </div>
  </div>
</template>
