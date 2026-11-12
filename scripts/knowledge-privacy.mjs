import { readFileSync } from 'node:fs';

import { JSON_SCHEMA, load } from 'js-yaml';

if (typeof import.meta.main !== 'boolean')
  throw new Error('Knowledge checks require the Node version in .node-version.');

const privateField = /^(?:cwd|repo_root|hostname|username|(?:\w+_)?(?:session|turn)_id)$/i;
const privatePath =
  /(?:\b[a-z]:[\\/]|(?<![\w./-])\/(?:Users|home)\/[^/\s]+|\\\\[^\\\s]+\\|file:\/\/|codex:\/\/threads?\/)/i;
const privateAssignment =
  /(?:^|[\s`{,])["']?(?:cwd|repo_root|hostname|username|(?:\w+_)?(?:session|turn)_id)["']?\s*[:=]\s*\S/im;
const privateMessage =
  'Note contains workstation or chat metadata; use repository-relative references.';

export function validateNote(text) {
  text = text.replace(/^\uFEFF/, '');
  if (text.includes('\0')) throw new Error('Knowledge notes must be UTF-8 Markdown.');
  if (privatePath.test(text) || privateAssignment.test(text)) throw new Error(privateMessage);

  if (!/^---[ \t]*(?:\r?\n|$)/.test(text)) return { metadata: {}, body: text };
  const match = /^---[ \t]*\r?\n([\s\S]*?)^---[ \t]*\r?$(?:\n|$)/m.exec(text);
  if (!match) throw new Error('Unterminated knowledge note frontmatter.');
  let metadata;
  try {
    metadata = load(match[1], { schema: JSON_SCHEMA, maxDepth: 20 });
  } catch {
    // YAML exceptions include a source excerpt that may contain the rejected value.
    throw new Error('Invalid knowledge note frontmatter.');
  }
  if (!metadata || typeof metadata !== 'object' || Array.isArray(metadata))
    throw new Error('Knowledge note frontmatter must be a mapping.');
  const seen = new Set();
  function inspect(value) {
    if (typeof value === 'string' && privatePath.test(value)) throw new Error(privateMessage);
    if (!value || typeof value !== 'object' || seen.has(value)) return;
    seen.add(value);
    for (const [key, item] of Object.entries(value)) {
      if (privateField.test(key)) throw new Error(privateMessage);
      inspect(item);
    }
  }
  inspect(metadata);
  return { metadata, body: text.slice(match[0].length) };
}

if (import.meta.main) {
  try {
    validateNote(readFileSync(0, 'utf8'));
  } catch (error) {
    console.error(error.message);
    process.exitCode = 1;
  }
}
