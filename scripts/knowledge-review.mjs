import { execFileSync } from 'node:child_process';
import { existsSync, readFileSync, realpathSync, statSync } from 'node:fs';
import { isAbsolute, join, relative, sep } from 'node:path';
import { parseArgs } from 'node:util';

import { validateNote } from './knowledge-privacy.mjs';

const knowledgeDirectory = 'docs/knowledge';
const snapshots = new Set(['coding_session', 'codex_session']);
const retired = new Set(['superseded', 'rejected', 'archived']);
const normalize = (text) => text.normalize('NFKC').toLowerCase();
const scalar = (value) => (typeof value === 'string' ? value : '');

function git(root, args) {
  return execFileSync('git', args, {
    cwd: root,
    encoding: 'utf8',
    windowsHide: true,
    maxBuffer: 16 * 1024 * 1024,
    stdio: ['ignore', 'pipe', 'pipe'],
  });
}

function rootOf(cwd) {
  return realpathSync(git(cwd, ['rev-parse', '--show-toplevel']).trim());
}

function checkStagedNotes(cwd) {
  const root = rootOf(cwd);
  const listed = git(root, [
    'diff',
    '--cached',
    '--diff-filter=ACMRT',
    '--name-only',
    '-z',
    '--',
    knowledgeDirectory,
  ]);
  for (const path of listed.split('\0').filter((name) => /\.md$/i.test(name))) {
    // :path reads stage zero, including when the working copy differs or is missing.
    const text = git(root, ['cat-file', 'blob', `:${path}`]);
    try {
      validateNote(text);
    } catch (error) {
      throw new Error(`${path}: ${error.message}`, { cause: error });
    }
  }
}

function inside(root, path) {
  const difference = relative(root, path);
  return difference !== '..' && !difference.startsWith(`..${sep}`) && !isAbsolute(difference);
}

function sourcePath(value) {
  if (typeof value !== 'string') throw new Error('sources entries must be strings');
  const path = value.replaceAll('\\', '/');
  if (
    !path ||
    path !== path.trim() ||
    path.startsWith('/') ||
    /[:*?#[\]]/.test(path) ||
    path.split('/').some((part) => !part || part === '.' || part === '..')
  )
    throw new Error(`sources must contain exact repo-relative file paths: ${path.slice(0, 120)}`);
  return path;
}

function readNotes(root) {
  const files = [
    ...new Set(
      git(root, [
        'ls-files',
        '-z',
        '--cached',
        '--others',
        '--exclude-standard',
        '--',
        knowledgeDirectory,
      ])
        .split('\0')
        .filter((path) => /\.md$/i.test(path)),
    ),
  ];
  const notes = [],
    warnings = [];
  for (const path of files) {
    const full = join(root, path);
    if (!existsSync(full)) continue;
    try {
      if (!inside(root, realpathSync(full))) throw new Error('note resolves outside this worktree');
      if (statSync(full).size > 256 * 1024) throw new Error('note exceeds the 256 KiB read limit');
      const text = readFileSync(full, 'utf8').replace(/^\uFEFF/, '');
      const { metadata, body } = validateNote(text);
      const type = scalar(metadata.type) || 'note';
      if (type === 'schema') continue;
      let sources = [];
      try {
        if (metadata.sources !== undefined && !Array.isArray(metadata.sources))
          throw new Error('sources must be an array');
        sources = [...new Set((metadata.sources ?? []).map(sourcePath))];
      } catch (error) {
        warnings.push({ path, message: error.message });
      }
      const status = scalar(metadata.status);
      notes.push({
        path,
        title: scalar(metadata.title) || path,
        type,
        status,
        permalink: scalar(metadata.permalink),
        sources,
        historical: snapshots.has(type) || retired.has(status),
        body,
      });
    } catch (error) {
      warnings.push({ path, message: error.message });
    }
  }
  return { notes, warnings };
}

function metadata(note) {
  const { body: _body, ...fields } = note;
  return { ...fields, sources: fields.sources.slice(0, 8), sourceCount: fields.sources.length };
}

function page(items, limit, offset) {
  return {
    total: items.length,
    items: items.slice(offset, offset + limit),
    nextOffset: offset + limit < items.length ? offset + limit : null,
  };
}

function bounds(limit, offset) {
  if (!Number.isInteger(limit) || limit < 1 || limit > 50) throw new Error('limit must be 1..50');
  if (!Number.isInteger(offset) || offset < 0)
    throw new Error('offset must be a nonnegative integer');
}

function changesSince(root, base) {
  const changed = new Set();
  const renames = [];
  const readDiff = (args) => {
    const fields = git(root, [
      'diff',
      '--name-status',
      '-z',
      '--find-renames',
      ...args,
      '--',
    ]).split('\0');
    for (let i = 0; i < fields.length && fields[i];) {
      const status = fields[i++],
        path = fields[i++];
      changed.add(path);
      if (status.startsWith('R') || status.startsWith('C')) {
        const destination = fields[i++];
        changed.add(destination);
        renames.push({ from: path, to: destination });
      }
    }
  };
  readDiff([base, 'HEAD']);
  readDiff(['--cached']);
  readDiff([]);
  for (const path of git(root, ['ls-files', '-z', '--others', '--exclude-standard'])
    .split('\0')
    .filter(Boolean))
    changed.add(path);
  return { paths: [...changed].sort(), renames };
}

export function checkKnowledge(cwd, { since = 'HEAD', limit = 10, offset = 0 } = {}) {
  bounds(limit, offset);
  const root = rootOf(cwd);
  const base = git(root, ['rev-parse', '--verify', '--end-of-options', `${since}^{commit}`]).trim();
  const changes = changesSince(root, base);
  const changed = new Set(changes.paths);
  const { notes, warnings } = readNotes(root);
  const candidates = [],
    unlinked = [];
  for (const note of notes.filter((entry) => !entry.historical)) {
    if (!note.sources.length) {
      unlinked.push(metadata(note));
      continue;
    }
    const changedSources = note.sources.filter((path) => changed.has(path));
    const missingSources = note.sources.filter((path) => !existsSync(join(root, path)));
    if (changedSources.length || missingSources.length)
      candidates.push({
        ...metadata(note),
        changedSources: changedSources.slice(0, 20),
        missingSources: missingSources.slice(0, 20),
        changedSourceCount: changedSources.length,
        missingSourceCount: missingSources.length,
      });
  }
  return {
    root,
    base,
    scope:
      'base..HEAD plus staged, unstaged and untracked changes; missing source paths are also reported',
    advisory:
      'Candidates require semantic review. No notes or review markers were written. Zero candidates does not prove all knowledge is current.',
    changedFiles: page(changes.paths, limit, offset),
    renames: page(changes.renames, limit, offset),
    candidates: page(candidates, limit, offset),
    unlinked: page(unlinked, limit, offset),
    historicalSkipped: notes.filter((note) => note.historical).length,
    complete: warnings.length === 0,
    warnings: page(warnings, limit, offset),
  };
}

export function findKnowledge(cwd, terms, { limit = 10, offset = 0 } = {}) {
  bounds(limit, offset);
  const queries = [...new Set(terms.map((term) => normalize(term.trim())).filter(Boolean))];
  if (!queries.length || queries.length > 8 || queries.some((term) => term.length > 100))
    throw new Error('Supply 1..8 literal terms, each at most 100 characters');
  const root = rootOf(cwd),
    { notes, warnings } = readNotes(root);
  const matches = [];
  for (const note of notes) {
    const title = normalize(note.title),
      permalink = normalize(note.permalink),
      body = normalize(note.body);
    const sources = normalize(note.sources.join('\n'));
    let score = 0;
    for (const query of queries) {
      if (title === query || permalink === query) score += 100;
      else if (title.includes(query) || permalink.includes(query)) score += 20;
      if (sources.includes(query)) score += 10;
      if (body.includes(query)) score += 1;
    }
    if (!score) continue;
    const lines = note.body.split(/\r?\n/);
    const index = lines.findIndex((line) =>
      queries.some((query) => normalize(line).includes(query)),
    );
    matches.push({
      ...metadata(note),
      score,
      excerpt: index >= 0 ? lines[index].slice(0, 180) : '',
    });
  }
  matches.sort(
    (a, b) =>
      b.score - a.score ||
      Number(a.historical) - Number(b.historical) ||
      a.path.localeCompare(b.path),
  );
  return {
    root,
    terms: queries,
    mode: 'literal substring; any term; repository files only, no semantic expansion',
    matches: page(matches, limit, offset),
    complete: warnings.length === 0,
    warnings: page(warnings, limit, offset),
  };
}

export function main(args = process.argv.slice(2), cwd = process.cwd()) {
  const { values, positionals } = parseArgs({
    args,
    allowPositionals: true,
    options: {
      since: { type: 'string' },
      limit: { type: 'string', default: '10' },
      offset: { type: 'string', default: '0' },
    },
  });
  const [command, ...terms] = positionals;
  if (command === 'staged' && terms.length === 0) return checkStagedNotes(cwd);
  const options = { limit: Number(values.limit), offset: Number(values.offset) };
  if (command === 'check' && terms.length === 0)
    return checkKnowledge(cwd, { ...options, since: values.since ?? 'HEAD' });
  if (command === 'find' && values.since === undefined) return findKnowledge(cwd, terms, options);
  throw new Error(
    'Usage: knowledge-review.mjs staged | check [--since <commit>] [--limit 10] [--offset 0] | find <literal terms...> [--limit 10] [--offset 0]',
  );
}

if (import.meta.main) {
  try {
    const result = main();
    if (result !== undefined) console.log(JSON.stringify(result, null, 2));
  } catch (error) {
    console.error(error.message);
    process.exitCode = 1;
  }
}
