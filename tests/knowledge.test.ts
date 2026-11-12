// @vitest-environment node
import { execFileSync, spawnSync } from 'node:child_process';
import {
  cpSync,
  mkdirSync,
  mkdtempSync,
  readFileSync,
  realpathSync,
  rmSync,
  symlinkSync,
  writeFileSync,
} from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, join, resolve, sep } from 'node:path';
import { fileURLToPath } from 'node:url';

import { afterEach, describe, expect, it } from 'vitest';

const source = fileURLToPath(new URL('../scripts/knowledge.mjs', import.meta.url));
const owned: string[] = [];

function git(cwd: string, args: string[]) {
  return execFileSync(
    'git',
    [
      '-c',
      'commit.gpgsign=false',
      '-c',
      'user.name=Memory Test',
      '-c',
      'user.email=memory-test@example.invalid',
      ...args,
    ],
    { cwd, encoding: 'utf8', stdio: ['ignore', 'pipe', 'pipe'] },
  );
}

function fixture() {
  const directory = realpathSync(mkdtempSync(join(tmpdir(), 'vue-memory-')));
  owned.push(directory);
  const root = join(directory, 'project with spaces');
  mkdirSync(join(root, 'scripts'), { recursive: true });
  mkdirSync(join(root, '.codex'));
  mkdirSync(join(root, 'src/nested'), { recursive: true });
  mkdirSync(join(directory, 'empty-hooks'));
  cpSync(source, join(root, 'scripts/knowledge.mjs'));
  writeFileSync(
    join(root, '.codex/basic-memory.json'),
    JSON.stringify({ basicMemory: { primaryProject: 'finevent' } }),
  );
  git(root, ['init', '--initial-branch=main']);
  git(root, ['config', 'core.hooksPath', join(directory, 'empty-hooks')]);
  const cache = join(directory, 'cache');
  return { directory, root, cache };
}

function run(root: string, cache: string, cwd = root, args = ['bind'], input?: string) {
  return spawnSync(process.execPath, [join(root, 'scripts/knowledge.mjs'), ...args], {
    cwd,
    env: { ...process.env, FINEVENT_MEMORY_CACHE: cache },
    encoding: 'utf8',
    input,
  });
}

function binding(
  root: string,
  cache: string,
  cwd = root,
): { settings: string; notes: string; root: string } {
  const result = run(root, cache, cwd);
  expect(result.stderr).toBe('');
  expect(result.status).toBe(0);
  return JSON.parse(result.stdout) as { settings: string; notes: string; root: string };
}

afterEach(() => {
  for (const directory of owned.splice(0)) {
    const absolute = resolve(directory);
    const temporaryRoot = realpathSync(tmpdir());
    if (
      !absolute.startsWith(`${temporaryRoot}${sep}vue-memory-`) ||
      realpathSync(absolute) !== absolute
    ) {
      throw new Error('Refusing to remove a directory outside the owned test fixture.');
    }
    rmSync(absolute, { recursive: true, force: true });
  }
});

describe('portable project notes', () => {
  it('removes the private compact request and preserves the project context', async () => {
    const { portableCheckpointBrief }: { portableCheckpointBrief: (brief: string) => string } =
      await import(new URL('../scripts/knowledge.mjs', import.meta.url).href);
    const context = '# Basic Memory\n\nCurrent project knowledge.';
    const upstream =
      'Basic Memory checkpoint required after compaction. Host-provided session metadata: {"session_id":"test-session","codex_turn_id":"test-turn"}. Pass these exact values.';
    const brief = portableCheckpointBrief(`${upstream}\r\n\r\n---\r\n\r\n${context}`);
    expect(brief).toContain('bm-checkpoint');
    expect(brief).not.toMatch(/test-session|test-turn|Pass these exact/);
    expect(brief.endsWith(context)).toBe(true);
    expect(portableCheckpointBrief(context)).toBe(context);
  });
});

describe('repo knowledge binding', () => {
  it('uses the same binding from the worktree root and a nested directory', () => {
    const { root, cache } = fixture();
    const first = binding(root, cache);
    const nested = binding(root, cache, join(root, 'src/nested'));
    expect(nested).toEqual(first);
    expect(first.notes).toBe(join(realpathSync(root), 'docs/knowledge'));
    expect(first.settings.startsWith(cache + sep)).toBe(true);
  });

  it('gives two worktrees separate configuration and knowledge paths', () => {
    const { directory, root, cache } = fixture();
    git(root, ['add', '.']);
    git(root, ['commit', '-m', 'Create fixture']);
    const other = join(directory, 'other worktree');
    git(root, ['worktree', 'add', '-b', 'test-other', other]);
    const first = binding(root, cache);
    const second = binding(other, cache);
    expect(second.settings).not.toBe(first.settings);
    expect(second.notes).toBe(join(realpathSync(other), 'docs/knowledge'));
    const config = JSON.parse(readFileSync(join(second.settings, 'config.json'), 'utf8')) as {
      projects: Record<string, { path: string }>;
    };
    expect(config.projects['finevent']?.path).toBe(second.notes);
  });

  it('rejects a copied configuration pointing at another directory without overwriting it', () => {
    const { directory, root, cache } = fixture();
    const current = binding(root, cache);
    const foreign = join(directory, 'foreign');
    mkdirSync(foreign);
    const path = join(current.settings, 'config.json');
    const changed = JSON.stringify({
      projects: { finevent: { path: foreign, mode: 'local' } },
      default_project: 'finevent',
    });
    writeFileSync(path, changed);
    const result = run(root, cache);
    expect(result.status).not.toBe(0);
    expect(result.stderr).toContain('outside its assigned worktree');
    expect(readFileSync(path, 'utf8')).toBe(changed);
  });

  it('rejects a knowledge junction or symlink outside the worktree', () => {
    const { directory, root, cache } = fixture();
    const outside = join(directory, 'outside');
    mkdirSync(outside);
    mkdirSync(join(root, 'docs'));
    symlinkSync(
      outside,
      join(root, 'docs/knowledge'),
      process.platform === 'win32' ? 'junction' : 'dir',
    );
    const result = run(root, cache);
    expect(result.status).not.toBe(0);
    expect(result.stderr).toContain('escapes this worktree');
  });

  it('does not bind an unrelated repository through this launcher', () => {
    const { directory, root, cache } = fixture();
    const other = join(directory, 'unrelated');
    mkdirSync(other);
    git(other, ['init']);
    const result = run(root, cache, other);
    expect(result.status).not.toBe(0);
    expect(result.stderr).toContain('own Git worktree');
  });

  it('rejects a stale binding manifest instead of silently moving the database', () => {
    const { root, cache } = fixture();
    const current = binding(root, cache);
    writeFileSync(
      join(current.settings, 'binding.json'),
      JSON.stringify({ worktree: dirname(root) }),
    );
    const result = run(root, cache);
    expect(result.status).not.toBe(0);
    expect(result.stderr).toContain('Stale worktree binding');
  });

  it('reports a missing runtime before attempting to start MCP', () => {
    const { root, cache } = fixture();
    const result = run(root, cache, root, ['mcp']);
    expect(result.status).not.toBe(0);
    expect(result.stdout).toBe('');
    expect(result.stderr).toContain('pnpm knowledge:setup');
  });
});
