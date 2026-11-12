import { spawn, execFileSync } from 'node:child_process';
import { randomUUID } from 'node:crypto';
import { mkdir } from 'node:fs/promises';
import { resolve } from 'node:path';
import process from 'node:process';

await mkdir('bin', { recursive: true });
const port = Number(process.argv[2] ?? 4173);
if (!Number.isInteger(port) || port < 1 || port > 65535) throw new Error('Invalid test port');
const executable = `bin/finevent-test-${port}${process.platform === 'win32' ? '.exe' : ''}`;
execFileSync('go', ['-C', 'apps/server', 'build', '-o', resolve(executable), './cmd/finevent'], {
  stdio: 'inherit',
});
const child = spawn(
  executable,
  [
    '-dev',
    '-calendar-snapshot',
    '-web',
    'apps/web/dist',
    '-data',
    `.validation/e2e-${randomUUID()}`,
    '-listen',
    `127.0.0.1:${port}`,
  ],
  {
    stdio: 'inherit',
    env: { ...process.env, FINEVENT_TEST_PASSWORD: 'finevent-e2e-password-only' },
  },
);
for (const signal of ['SIGINT', 'SIGTERM']) process.on(signal, () => child.kill());
child.on('exit', (code) => process.exit(code ?? 0));
