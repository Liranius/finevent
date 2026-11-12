// @vitest-environment node
import { execFileSync, spawnSync } from 'node:child_process';
import {
  chmodSync,
  copyFileSync,
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

type Page<T> = { total: number; items: T[]; nextOffset: number | null };
type Note = {
  title: string;
  path: string;
  status: string;
  historical: boolean;
  excerpt: string;
  missingSources: string[];
};
type Options = { since?: string; limit?: number; offset?: number };
const script = fileURLToPath(new URL('../scripts/knowledge-review.mjs', import.meta.url));
function invoke(root: string, command: string, terms: string[], options: Options) {
  const args = Object.entries(options).flatMap(([key, value]) => [`--${key}`, String(value)]);
  return JSON.parse(
    execFileSync(process.execPath, [script, command, ...terms, ...args], {
      cwd: root,
      encoding: 'utf8',
      stdio: ['ignore', 'pipe', 'pipe'],
    }),
  ) as unknown;
}
function checkKnowledge(root: string, options: Options = {}) {
  return invoke(root, 'check', [], options) as {
    candidates: Page<Note>;
    renames: Page<{ from: string; to: string }>;
    unlinked: Page<Note>;
    warnings: Page<{ message: string }>;
    complete: boolean;
    historicalSkipped: number;
  };
}
function findKnowledge(root: string, terms: string[], options: Options = {}) {
  return invoke(root, 'find', terms, options) as {
    root: string;
    matches: Page<Note>;
    warnings: Page<{ message: string }>;
  };
}

const owned: string[] = [];
function git(root: string, args: string[]) {
  return execFileSync(
    'git',
    [
      '-c',
      'commit.gpgsign=false',
      '-c',
      'user.name=Knowledge Test',
      '-c',
      'user.email=knowledge@example.invalid',
      ...args,
    ],
    { cwd: root, encoding: 'utf8', stdio: ['ignore', 'pipe', 'pipe'] },
  ).trim();
}
function put(root: string, path: string, content: string) {
  mkdirSync(dirname(join(root, path)), { recursive: true });
  writeFileSync(join(root, path), content);
}
function note(
  root: string,
  name: string,
  sources: string[] = [],
  extra = '',
  body = 'Current reference',
) {
  put(
    root,
    `docs/knowledge/${name}.md`,
    `---\ntitle: ${name}\ntype: decision\nstatus: accepted\nsources: ${JSON.stringify(sources)}\n${extra}---\n${body}\n`,
  );
}
function commit(root: string) {
  git(root, ['add', '.']);
  git(root, ['commit', '-m', 'Fixture state']);
  return git(root, ['rev-parse', 'HEAD']);
}
function fixture() {
  const root = realpathSync(mkdtempSync(join(tmpdir(), 'knowledge-review-')));
  owned.push(root);
  git(root, ['init', '--initial-branch=main']);
  mkdirSync(join(root, '.empty-hooks'));
  git(root, ['config', 'core.hooksPath', join(root, '.empty-hooks')]);
  put(root, '.gitignore', 'ignored/\n');
  put(root, 'src/等待 policy.mjs', 'export const wait = 30;\n');
  commit(root);
  return root;
}
afterEach(() => {
  for (const root of owned.splice(0)) {
    const path = resolve(root);
    if (
      !path.startsWith(`${realpathSync(tmpdir())}${sep}knowledge-review-`) ||
      realpathSync(path) !== path
    )
      throw new Error('Not an owned fixture');
    rmSync(path, { recursive: true, force: true });
  }
});

describe('knowledge impact checks', () => {
  it('combines staged, unstaged and untracked paths without modifying notes', () => {
    const root = fixture();
    put(root, 'src/deleted.mjs', 'old');
    note(root, '等待', ['src/等待 policy.mjs']);
    note(root, '删除', ['src/deleted.mjs']);
    note(root, '新增', ['src/new.mjs']);
    commit(root);
    const before = readFileSync(join(root, 'docs/knowledge/等待.md'), 'utf8');
    put(root, 'src/等待 policy.mjs', 'export const wait = 45;\n');
    git(root, ['add', 'src/等待 policy.mjs']);
    // Reverting the staged edit in the worktree must still report the staged change.
    put(root, 'src/等待 policy.mjs', 'export const wait = 30;\n');
    rmSync(join(root, 'src/deleted.mjs'));
    put(root, 'src/new.mjs', 'new');
    const result = checkKnowledge(root);
    expect(result.candidates.items.map((entry) => entry.title).sort()).toEqual(
      ['删除', '新增', '等待'].sort(),
    );
    expect(result.candidates.items.find((entry) => entry.title === '删除')?.missingSources).toEqual(
      ['src/deleted.mjs'],
    );
    expect(readFileSync(join(root, 'docs/knowledge/等待.md'), 'utf8')).toBe(before);
  });

  it('includes committed changes only when an earlier baseline is selected', () => {
    const root = fixture();
    note(root, '等待', ['src/等待 policy.mjs']);
    const baseline = commit(root);
    put(root, 'src/等待 policy.mjs', 'export const wait = 45;\n');
    commit(root);
    expect(checkKnowledge(root).candidates.total).toBe(0);
    expect(checkKnowledge(root, { since: baseline }).candidates.items[0]?.title).toBe('等待');
    expect(() => checkKnowledge(root, { since: 'missing-ref' })).toThrow(/rev-parse/);
  });

  it('reports both old and new source paths on rename', () => {
    const root = fixture();
    note(root, '旧路径', ['src/等待 policy.mjs']);
    note(root, '新路径', ['src/renamed.mjs']);
    commit(root);
    git(root, ['mv', 'src/等待 policy.mjs', 'src/renamed.mjs']);
    const result = checkKnowledge(root);
    expect(result.candidates.total).toBe(2);
    expect(result.renames.items).toContainEqual({
      from: 'src/等待 policy.mjs',
      to: 'src/renamed.mjs',
    });
  });

  it('keeps snapshots and retired decisions out of maintenance candidates', () => {
    const root = fixture();
    for (const type of ['coding_session', 'codex_session', 'schema'])
      put(
        root,
        `docs/knowledge/${type}.md`,
        `---\ntitle: ${type}\ntype: ${type}\nsources: [src/等待 policy.mjs]\n---\nPast fact`,
      );
    put(
      root,
      'docs/knowledge/retired.md',
      '---\ntype: decision\nstatus: superseded\nsources: [src/等待 policy.mjs]\n---\nOld rule',
    );
    note(root, '未批准', ['src/等待 policy.mjs']);
    put(
      root,
      'docs/knowledge/proposal.md',
      '---\ntype: decision\nstatus: open\nsources: [src/等待 policy.mjs]\n---\nProposal',
    );
    commit(root);
    put(root, 'src/等待 policy.mjs', 'new implementation');
    const result = checkKnowledge(root);
    expect(result.candidates.total).toBe(2);
    expect(result.candidates.items.some((entry) => entry.status === 'open')).toBe(true);
    expect(result.historicalSkipped).toBe(3);
  });

  it('flags private note fields, local paths and chat links without printing their values', () => {
    const root = fixture();
    note(root, '会话', [], 'codex_turn_id: private-turn\n');
    note(root, '路径', [], '', 'See C:\\Users\\private-account\\repo');
    note(root, '聊天', [], '', 'codex://threads/private-chat');
    note(root, '可恢复', [], '', 'Open docs/knowledge/codex/checkpoints or a GitHub PR.');
    const result = checkKnowledge(root);
    expect(result.warnings.total).toBe(3);
    expect(result.complete).toBe(false);
    expect(JSON.stringify(result.warnings)).not.toMatch(
      /private-turn|private-account|private-chat/,
    );
  });

  it('validates the final note including nested metadata, malformed YAML and body links', () => {
    const validator = fileURLToPath(new URL('../scripts/knowledge-privacy.mjs', import.meta.url));
    const run = (input: string) =>
      spawnSync(process.execPath, [validator], { input, encoding: 'utf8' });
    for (const input of [
      '---\r\nmetadata: [{ "session_id": "private-value" }]\r\n---\r\n',
      '---\ntitle: [private-value\n---\n',
      '---\ntype: schema\n---\n[chat](codex://thread/private-value)',
      '---\nmeta: {"user\\u006eame": "private-value"}\n---\n',
      'See </home/private-value/project>',
      'See file:///private-value/doc.md',
      'Host field: hostname=private-value',
    ]) {
      const result = run(input);
      expect(result.status).toBe(1);
      expect(result.stderr).not.toContain('private-value');
      expect(result.stdout).toBe('');
    }
    expect(run('---\ntitle: 交接\n---\n[PR](https://github.com/example/repo/pull/6)').status).toBe(
      0,
    );
  });

  it('checks staged blobs even when the working file differs, moves or disappears', () => {
    const root = fixture();
    const path = 'docs/knowledge/交接 笔记.md';
    const staged = () =>
      spawnSync(process.execPath, [script, 'staged'], { cwd: root, encoding: 'utf8' });
    put(root, path, 'codex://threads/private-value');
    git(root, ['add', path]);
    put(root, path, '已清理');
    const rejected = staged();
    expect(rejected.status).toBe(1);
    expect(rejected.stderr).toContain(path);
    expect(rejected.stderr).not.toContain('private-value');
    rmSync(join(root, path));
    expect(staged().status).toBe(1);
    put(root, path, '已清理');
    git(root, ['add', path]);
    put(root, path, 'codex://threads/unstaged');
    expect(staged().status).toBe(0);
    git(root, ['mv', path, 'docs/knowledge/renamed.MD']);
    git(root, ['add', 'docs/knowledge/renamed.MD']);
    expect(staged().status).toBe(1);
    git(root, ['rm', '-f', 'docs/knowledge/renamed.MD']);
    expect(staged().status).toBe(0);
  });

  it('rejects a Git commit through the configured Lefthook privacy job and accepts a clean retry', () => {
    const root = fixture();
    const path = 'docs/knowledge/handoff.md';
    put(root, path, 'Original note\n');
    const head = commit(root);
    const repo = dirname(dirname(script));
    copyFileSync(join(repo, 'package.json'), join(root, 'package.json'));
    copyFileSync(
      fileURLToPath(new URL('../lefthook.yml', import.meta.url)),
      join(root, 'lefthook.yml'),
    );
    symlinkSync(
      dirname(script),
      join(root, 'scripts'),
      process.platform === 'win32' ? 'junction' : 'dir',
    );
    symlinkSync(
      join(repo, 'node_modules'),
      join(root, 'node_modules'),
      process.platform === 'win32' ? 'junction' : 'dir',
    );
    const hook = join(root, '.empty-hooks/pre-commit');
    writeFileSync(
      hook,
      '#!/bin/sh\nexec node "$TEST_LEFTHOOK" run pre-commit --job knowledge-privacy --job format --job knowledge-privacy-final --no-auto-install\n',
    );
    chmodSync(hook, 0o755);
    const commitNote = () =>
      spawnSync(
        'git',
        [
          '-c',
          'user.name=Knowledge Test',
          '-c',
          'user.email=knowledge@example.invalid',
          '-c',
          'commit.gpgsign=false',
          'commit',
          '-m',
          'docs: record checkpoint',
        ],
        {
          cwd: root,
          encoding: 'utf8',
          env: { ...process.env, TEST_LEFTHOOK: fileURLToPath(import.meta.resolve('lefthook')) },
        },
      );
    put(root, path, '---\r\nhostname: private-value\r\n---\r\nNote\r\n');
    git(root, ['add', path]);
    put(root, path, 'Repository-relative reference\n');
    const rejected = commitNote();
    expect(rejected.status).not.toBe(0);
    expect(rejected.stdout + rejected.stderr).toContain(
      'Note contains workstation or chat metadata',
    );
    expect(rejected.stdout + rejected.stderr).not.toContain('private-value');
    expect(git(root, ['rev-parse', 'HEAD'])).toBe(head);
    expect(readFileSync(join(root, path), 'utf8')).toBe('Repository-relative reference\n');
    git(root, ['add', path]);
    expect(commitNote().status).toBe(0);
    expect(git(root, ['show', `HEAD:${path}`])).toBe('Repository-relative reference');
  }, 15000);

  it('checks the repository knowledge notes that will go through Git review', () => {
    const root = fileURLToPath(new URL('..', import.meta.url));
    let offset: number | null = 0;
    do {
      const { warnings } = checkKnowledge(root, { limit: 50, offset });
      expect(
        warnings.items.filter(({ message }) =>
          message.startsWith('Note contains workstation or chat metadata'),
        ),
      ).toEqual([]);
      offset = warnings.nextOffset;
    } while (offset !== null);
  });

  it('reports missing associations and malformed frontmatter without claiming a complete check', () => {
    const root = fixture();
    note(root, '未关联');
    note(root, '非法来源', ['../outside.txt']);
    put(root, 'docs/knowledge/broken.md', '---\ntitle: [invalid\n---\ntext');
    const result = checkKnowledge(root);
    expect(result.unlinked.total).toBe(2);
    expect(result.warnings.total).toBe(2);
    expect(result.complete).toBe(false);
  });

  it('does not read a linked note outside the repository', () => {
    const root = fixture(),
      outside = fixture();
    put(outside, 'private.md', '---\ntitle: secret\n---\nprivate');
    mkdirSync(join(root, 'docs/knowledge'), { recursive: true });
    symlinkSync(
      outside,
      join(root, 'docs/knowledge/external'),
      process.platform === 'win32' ? 'junction' : 'dir',
    );
    const result = findKnowledge(root, ['private']);
    expect(result.matches.total).toBe(0);
    // Git may omit linked directories; if it enumerates them, the realpath check reports the skip.
    expect(result.warnings.items.every((entry) => entry.message.includes('outside'))).toBe(true);
  });
});

describe('bounded local retrieval fallback', () => {
  it('finds Chinese substrings with any-term semantics and preserves decision status', () => {
    const root = fixture();
    note(root, '等待规则', ['src/等待 policy.mjs'], '', '请求超时后允许手动操作。');
    put(
      root,
      'docs/knowledge/proposal.md',
      '---\ntitle: 未批准提议\ntype: decision\nstatus: open\n---\n建议增加自动重试，尚未批准。',
    );
    put(root, 'ignored/unrelated.md', '超时 重试');
    const result = findKnowledge(join(root, 'src'), ['超时', '重试']);
    expect(result.matches.total).toBe(2);
    expect(result.matches.items.find((entry) => entry.title === '未批准提议')?.status).toBe('open');
    expect(result.matches.items.every((entry) => entry.excerpt.length <= 180)).toBe(true);
    expect(result.root).toBe(root);
  });

  it('paginates metadata without returning full note bodies', () => {
    const root = fixture();
    for (let i = 0; i < 7; i++) note(root, `Note${i}`, [], '', '超时 ' + 'long body '.repeat(500));
    const first = findKnowledge(root, ['超时'], { limit: 3 });
    const second = findKnowledge(root, ['超时'], { limit: 3, offset: 3 });
    expect(first.matches.total).toBe(7);
    expect(first.matches.nextOffset).toBe(3);
    expect(second.matches.items).toHaveLength(3);
    expect(
      new Set([...first.matches.items, ...second.matches.items].map((entry) => entry.path)).size,
    ).toBe(6);
    expect(JSON.stringify(first)).not.toContain('body":"');
    expect(() => findKnowledge(root, ['超时'], { limit: 51 })).toThrow('limit must be 1..50');
  });

  it('finds existing notes by exact permalink without changing historical snapshots', () => {
    const root = fixture();
    put(
      root,
      'docs/knowledge/snapshot.md',
      '---\ntitle: Snapshot\ntype: coding_session\npermalink: test/checkpoint\n---\nOriginal state',
    );
    const before = readFileSync(join(root, 'docs/knowledge/snapshot.md'), 'utf8');
    const result = findKnowledge(root, ['test/checkpoint']);
    expect(result.matches.items[0]?.historical).toBe(true);
    expect(readFileSync(join(root, 'docs/knowledge/snapshot.md'), 'utf8')).toBe(before);
  });
});
