import { createRouter, createWebHistory } from 'vue-router'
import LobbyView from './views/LobbyView.vue'
import GameView from './views/GameView.vue'

export const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', component: LobbyView },
    { path: '/game/:id', component: GameView },
  ],
})
