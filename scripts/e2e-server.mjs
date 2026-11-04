import { spawn, execFileSync } from 'node:child_process';
import { randomUUID } from 'node:crypto';
import { mkdir } from 'node:fs/promises';
import { resolve } from 'node:path';
import process from 'node:process';

await mkdir('bin', { recursive: true });
const executable = process.platform === 'win32' ? 'bin/finevent-test.exe' : 'bin/finevent-test';
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
    '127.0.0.1:4173',
  ],
  {
    stdio: 'inherit',
    env: { ...process.env, FINEVENT_TEST_PASSWORD: 'finevent-e2e-password-only' },
  },
);
for (const signal of ['SIGINT', 'SIGTERM']) process.on(signal, () => child.kill());
child.on('exit', (code) => process.exit(code ?? 0));
