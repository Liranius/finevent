import { spawn } from 'node:child_process';
import { randomUUID } from 'node:crypto';
import { createServer } from 'node:net';
import { resolve } from 'node:path';
import { setTimeout } from 'node:timers/promises';

import { startPreview } from '../apps/web/scripts/test-preview.ts';

export async function startOfflineServer(
  projectURL: string | undefined,
  webRoot = 'apps/web/dist',
  previewPort = 0,
) {
  if (!projectURL) throw new Error('Missing project base URL');
  const reservation = createServer();
  await new Promise<void>((ready, reject) => {
    reservation.once('error', reject);
    reservation.listen(0, '127.0.0.1', ready);
  });
  const address = reservation.address();
  if (!address || typeof address === 'string') throw new Error('Missing test server port');
  await new Promise<void>((done) => reservation.close(() => done()));

  const port = address.port;
  const projectPort = new URL(projectURL).port;
  const executable = resolve(
    `bin/finevent-test-${projectPort}${process.platform === 'win32' ? '.exe' : ''}`,
  );
  const backend = 'http://127.0.0.1:' + port;
  const preview = await startPreview(previewPort, backend, webRoot);
  const publicAddress = preview.httpServer.address();
  if (!publicAddress || typeof publicAddress === 'string') throw new Error('Missing preview port');
  const origin = 'http://127.0.0.1:' + publicAddress.port;
  const child = spawn(
    executable,
    [
      '-dev',
      '-origin',
      origin,
      '-calendar-snapshot',
      '-data',
      `.validation/offline-${randomUUID()}`,
      '-listen',
      `127.0.0.1:${port}`,
    ],
    {
      windowsHide: true,
      env: { ...process.env, FINEVENT_TEST_PASSWORD: 'finevent-e2e-password-only' },
      stdio: ['ignore', 'ignore', 'pipe'],
    },
  );
  let failure = '';
  child.stderr.on('data', (chunk: Buffer) => {
    failure = (failure + chunk.toString()).slice(-2000);
  });
  child.once('error', (error) => {
    failure = error.message;
  });
  let exited = false;
  const closed = new Promise<void>((done) =>
    child.once('close', () => {
      exited = true;
      done();
    }),
  );
  const stop = async () => {
    if (!exited) child.kill();
    await preview.close();
    await closed;
  };
  const url = origin + '/finevent/';
  try {
    const deadline = Date.now() + 10000;
    while (Date.now() < deadline && !exited) {
      const ready = await fetch(backend + '/finevent/healthz', {
        signal: AbortSignal.timeout(1000),
      }).then(
        (response) => response.ok,
        () => false,
      );
      if (ready) return { url, stop, closed };
      await setTimeout(50);
    }
    throw new Error(`Offline test server did not start: ${failure}`);
  } catch (error) {
    await stop();
    throw error;
  }
}
