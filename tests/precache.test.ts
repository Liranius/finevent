import { spawnSync } from 'node:child_process';
import { mkdtempSync, mkdirSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';

import { afterEach, describe, expect, it } from 'vitest';

const script = fileURLToPath(new URL('../apps/web/scripts/precache.mjs', import.meta.url));
const worker = readFileSync(new URL('../apps/web/public/sw.js', import.meta.url), 'utf8');
const directories: string[] = [];

function fixture() {
  const directory = mkdtempSync(join(tmpdir(), 'finevent-precache-'));
  directories.push(directory);
  mkdirSync(join(directory, 'dist/assets'), { recursive: true });
  for (const file of ['index.html', 'icon.svg', 'manifest.webmanifest'])
    writeFileSync(join(directory, 'dist', file), 'fixture');
  writeFileSync(join(directory, 'dist/sw.js'), worker);
  writeFileSync(join(directory, 'dist/assets/app.js'), 'const version = 1;');
  return directory;
}

function build(directory: string) {
  const result = spawnSync(process.execPath, [script], { cwd: directory, encoding: 'utf8' });
  expect({ status: result.status, stderr: result.stderr }).toEqual({ status: 0, stderr: '' });
  return {
    manifest: readFileSync(join(directory, 'dist/precache.json'), 'utf8'),
    worker: readFileSync(join(directory, 'dist/sw.js'), 'utf8'),
  };
}

afterEach(() => {
  for (const directory of directories.splice(0)) rmSync(directory, { recursive: true });
});

describe('PWA release updates', () => {
  it('changes worker bytes when only an application asset changes', () => {
    const directory = fixture();
    const first = build(directory);
    writeFileSync(join(directory, 'dist/assets/app.js'), 'const version = 2;');
    const second = build(directory);
    expect(second.manifest).not.toBe(first.manifest);
    expect(second.worker).not.toBe(first.worker);
    expect(second.worker).toContain(worker);
  });

  it('keeps repeated builds of identical content deterministic', () => {
    const directory = fixture();
    expect(build(directory)).toEqual(build(directory));
  });
});
