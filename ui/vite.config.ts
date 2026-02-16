import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      '/api': {
        target: 'http://rpi:8000',
        changeOrigin: true,
      },
      '/ws': {
        target: 'ws://rpi:8000',
        ws: true,
      },
    },
  },
  build: {
    outDir: '../static',
    emptyOutDir: false,
  },
})
