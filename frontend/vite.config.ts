import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

export default defineConfig({
  plugins: [vue()],
  server: {
    proxy: {
      '/games': {
        target: 'http://localhost:8000',
        ws: true,           // upgrade /games/{id}/ws to WebSocket
      },
      '/me': 'http://localhost:8000',
    },
  },
  build: {
    outDir: 'dist',
    emptyOutDir: true,
  },
})
