import { execFileSync } from 'node:child_process';
import { mkdir } from 'node:fs/promises';
import { resolve } from 'node:path';
import process from 'node:process';

import { startOfflineServer } from '../e2e/offline-server.ts';

await mkdir('bin', { recursive: true });
const port = Number(process.argv[2] ?? 4173);
if (!Number.isInteger(port) || port < 1 || port > 65535) throw new Error('Invalid test port');
const executable = `bin/finevent-test-${port}${process.platform === 'win32' ? '.exe' : ''}`;
execFileSync('go', ['-C', 'apps/server', 'build', '-o', resolve(executable), './cmd/finevent'], {
  stdio: 'inherit',
});
const server = await startOfflineServer('http://127.0.0.1:' + port, 'apps/web/dist', port);
for (const signal of ['SIGINT', 'SIGTERM']) process.once(signal, () => void server.stop());
await server.closed;
await server.stop();
