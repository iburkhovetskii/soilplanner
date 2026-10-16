import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import path from 'path'

export default defineConfig({
  // Базовый путь сборки. Для хостинга под суб-путём задаётся через env,
  // напр. VITE_BASE_PATH=/soil-planner/ . По умолчанию '/' (локалка/докер).
  base: process.env.VITE_BASE_PATH || '/',
  plugins: [react()],
  resolve: {
    alias: {
      '@': path.resolve(__dirname, './src'),
    },
  },
  server: {
    port: 5173,
    proxy: {
      '/api': {
        // В docker-compose backend доступен по сервис-хосту `backend`.
        // Для локального запуска без docker — берём localhost.
        target: process.env.VITE_API_PROXY || 'http://localhost:8000',
        changeOrigin: true,
      },
    },
  },
})
