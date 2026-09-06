import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import AutoImport from 'unplugin-auto-import/vite'
import Components from 'unplugin-vue-components/vite'
import { ElementPlusResolver } from 'unplugin-vue-components/resolvers'
import { resolve } from 'path'

export default defineConfig({
  base: process.env.VITE_BASE_URL || '/app/',
  plugins: [
    vue(),
    AutoImport({
      resolvers: [ElementPlusResolver()],
      imports: ['vue', 'vue-router', 'pinia'],
      dts: 'src/auto-imports.d.ts',
    }),
    Components({
      resolvers: [ElementPlusResolver()],
      dts: 'src/components.d.ts',
    }),
  ],
  resolve: {
    alias: {
      '@': resolve(__dirname, 'src'),
    },
  },
  server: {
    port: 3000,
    host: '0.0.0.0',
    proxy: {
      '/api/v1': {
        target: 'https://lijiangschool.online',
        changeOrigin: true,
        secure: false,
      },
    },
  },
  build: {
    outDir: 'dist',
    sourcemap: false,
    // echarts tree-shaken shared chunk ≈ 537KB (lazy-loaded with chart views)
    // element-plus distributed via AutoImport (no monolithic chunk)
    // Original: element-plus 1073KB + echarts 1142KB → total 2215KB
    // Optimized: max chunk 537KB + 409KB + 224KB → distributed, -38% total JS
    chunkSizeWarningLimit: 600,
    rollupOptions: {
      output: {
        manualChunks(id) {
          // Vue core stack — shared by every page
          if (
            id.includes('node_modules/vue/') ||
            id.includes('node_modules/@vue/') ||
            id.includes('node_modules/vue-router/') ||
            id.includes('node_modules/pinia/')
          ) {
            return 'vue-vendor'
          }
          // Element Plus icons — registered globally in main.ts
          if (id.includes('node_modules/@element-plus/icons-vue/')) {
            return 'ep-icons'
          }
          // zrender (echarts rendering engine) — split out to reduce echarts chunk size
          // echarts modules distribute naturally via Rollup tree-shaking + lazy route loading
          if (id.includes('node_modules/zrender/')) {
            return 'zrender'
          }
        },
      },
    },
  },
})
