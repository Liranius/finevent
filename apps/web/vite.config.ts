import { fileURLToPath, URL } from 'node:url';

import vue from '@vitejs/plugin-vue';
import { defineConfig } from 'vite';
import vueDevTools from 'vite-plugin-vue-devtools';

const apiTarget = process.env.FINEVENT_API_TARGET ?? 'http://127.0.0.1:8787';

export default defineConfig({
  base: '/finevent/',
  plugins: [vue(), vueDevTools()],
  resolve: { alias: { '@': fileURLToPath(new URL('./src', import.meta.url)) } },
  server: { proxy: { '/finevent/api/': apiTarget, '/finevent/healthz': apiTarget } },
  preview: { cors: false },
  build: { manifest: true },
});
