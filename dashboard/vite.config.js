import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

export default defineConfig({
  plugins: [react()],
  base: '/dashboard/',
  build: {
    rollupOptions: {
      output: {
        manualChunks(id) {
          if (!id.includes('node_modules')) return
          if (id.includes('/recharts/')) return 'charts'
          if (id.includes('/d3-') || id.includes('/victory-vendor/')) {
            return 'charts-vendor'
          }
          if (id.includes('/react/') || id.includes('/react-dom/')) {
            return 'react'
          }
        },
      },
    },
  },
  server: {
    proxy: {
      '/api': 'http://127.0.0.1:8000',
    },
  },
})
