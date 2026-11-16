import { resolve } from 'node:path';

import { preview } from 'vite';

export async function startPreview(port: number, backend: string, webRoot: string) {
  return preview({
    configFile: false,
    base: '/finevent/',
    appType: 'mpa',
    root: resolve('apps/web'),
    build: { outDir: resolve(webRoot) },
    preview: {
      cors: false,
      host: '127.0.0.1',
      port,
      strictPort: true,
      proxy: { '/finevent/api/': backend, '/finevent/healthz': backend },
    },
  });
}
