import { spawn, spawnSync, execFileSync } from 'node:child_process';
import { createHash, randomUUID } from 'node:crypto';
import {
  existsSync,
  mkdirSync,
  readFileSync,
  realpathSync,
  renameSync,
  writeFileSync,
} from 'node:fs';
import { homedir } from 'node:os';
import { dirname, isAbsolute, join, relative, resolve } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const version = '0.24.1';
const windows = process.platform === 'win32';
const sourceRoot = realpathSync(fileURLToPath(new URL('..', import.meta.url)));
const guardedRuntime = join(sourceRoot, 'scripts/knowledge-runtime.py');
const canonical = (path) => (windows ? path.toLowerCase() : path);

function inside(root, path) {
  const difference = relative(root, path);
  return (
    difference !== '..' &&
    !difference.startsWith(`..${windows ? '\\' : '/'}`) &&
    !isAbsolute(difference)
  );
}

function checkKnowledgePath(root, notes) {
  let ancestor = notes;
  while (!existsSync(ancestor)) ancestor = dirname(ancestor);
  if (!inside(root, realpathSync(ancestor))) {
    throw new Error('Knowledge directory escapes this worktree (possibly through a symlink).');
  }
}

function context(cwd) {
  const root = realpathSync(
    execFileSync('git', ['rev-parse', '--show-toplevel'], {
      cwd,
      encoding: 'utf8',
      windowsHide: true,
      stdio: ['ignore', 'pipe', 'pipe'],
    }).trim(),
  );
  if (canonical(root) !== canonical(sourceRoot)) {
    throw new Error('Run the knowledge launcher from its own Git worktree.');
  }
  const project = JSON.parse(readFileSync(join(root, '.codex/basic-memory.json'), 'utf8'))
    .basicMemory.primaryProject;
  if (typeof project !== 'string' || !/^[a-z0-9][a-z0-9_-]*$/.test(project)) {
    throw new Error('Invalid primaryProject in .codex/basic-memory.json.');
  }
  const cache = resolve(
    process.env.FINEVENT_MEMORY_CACHE ?? join(homedir(), '.cache/finevent-memory'),
  );
  const key = createHash('sha256').update(canonical(root)).digest('hex').slice(0, 20);
  const settings = join(cache, 'worktrees', key);
  const notes = join(root, 'docs/knowledge');
  checkKnowledgePath(root, notes);
  const runtime = join(cache, `runtime-${version}`);
  const python = join(runtime, windows ? 'Scripts/python.exe' : 'bin/python');
  const executable = join(runtime, windows ? 'Scripts/basic-memory.exe' : 'bin/basic-memory');
  return { root, project, notes, cache, settings, runtime, python, executable };
}

function writeNewJson(path, value) {
  if (existsSync(path)) return;
  const temporary = `${path}.${randomUUID()}.tmp`;
  writeFileSync(temporary, `${JSON.stringify(value, null, 2)}\n`);
  // Concurrent MCP and hook startup produce identical initial bindings.
  renameSync(temporary, path);
}

function bind(cwd) {
  const ctx = context(cwd);
  mkdirSync(ctx.notes, { recursive: true });
  mkdirSync(ctx.settings, { recursive: true });
  const bindingPath = join(ctx.settings, 'binding.json');
  const expected = { worktree: ctx.root, project: ctx.project, knowledge: ctx.notes };
  writeNewJson(bindingPath, expected);
  const binding = JSON.parse(readFileSync(bindingPath, 'utf8'));
  if (JSON.stringify(binding) !== JSON.stringify(expected)) {
    throw new Error(
      `Stale worktree binding: ${bindingPath}. Inspect this generated cache before resetting it.`,
    );
  }
  const configPath = join(ctx.settings, 'config.json');
  writeNewJson(configPath, {
    projects: { [ctx.project]: { path: ctx.notes, mode: 'local' } },
    default_project: ctx.project,
    auto_update: false,
  });
  const config = JSON.parse(readFileSync(configPath, 'utf8'));
  const configured = config.projects?.[ctx.project];
  if (
    Object.keys(config.projects ?? {}).length !== 1 ||
    configured?.mode !== 'local' ||
    typeof configured.path !== 'string' ||
    !isAbsolute(configured.path) ||
    canonical(realpathSync(configured.path)) !== canonical(realpathSync(ctx.notes)) ||
    config.default_project !== ctx.project
  ) {
    throw new Error(`Knowledge project points outside its assigned worktree: ${configPath}`);
  }
  return ctx;
}

function environment(ctx) {
  return {
    ...process.env,
    FINEVENT_NODE: process.execPath,
    BASIC_MEMORY_CONFIG_DIR: ctx.settings,
    BASIC_MEMORY_FORCE_LOCAL: 'true',
    BASIC_MEMORY_NO_PROMOS: '1',
    BASIC_MEMORY_AUTO_UPDATE: 'false',
    BASIC_MEMORY_SEMANTIC_EMBEDDING_CACHE_DIR: join(ctx.cache, 'models'),
    HF_HOME: join(ctx.cache, 'huggingface'),
    UV_CACHE_DIR: join(ctx.cache, 'uv-cache'),
    UV_PYTHON_INSTALL_DIR: join(ctx.cache, 'python'),
    PYTHONUTF8: '1',
  };
}

function runSync(ctx, executable, args, options = {}) {
  const result = spawnSync(executable, args, {
    cwd: ctx.root,
    env: environment(ctx),
    encoding: 'utf8',
    windowsHide: true,
    stdio: 'inherit',
    ...options,
  });
  if (result.error) throw result.error;
  if (result.status !== 0)
    throw new Error(`${executable} exited ${result.status}: ${result.stderr ?? ''}`);
  return result.stdout;
}

function installedVersion(ctx) {
  if (!existsSync(ctx.python)) return null;
  const result = spawnSync(
    ctx.python,
    ['-c', 'from importlib.metadata import version; print(version("basic-memory"))'],
    {
      encoding: 'utf8',
      windowsHide: true,
    },
  );
  return result.status === 0 ? result.stdout.trim() : null;
}

function requireRuntime(ctx) {
  if (!existsSync(ctx.executable) || installedVersion(ctx) !== version) {
    throw new Error(`Basic Memory ${version} is not ready. Run pnpm knowledge:setup first.`);
  }
}

function findUv(ctx) {
  for (const command of [join(ctx.cache, 'bin', windows ? 'uv.exe' : 'uv'), 'uv']) {
    const result = spawnSync(command, ['--version'], { encoding: 'utf8', windowsHide: true });
    if (result.status === 0) return command;
  }
  throw new Error('Install uv first (see README: 项目知识), then run pnpm knowledge:setup.');
}

async function relay(ctx, executable, args, input) {
  const child = spawn(executable, args, {
    cwd: ctx.root,
    env: environment(ctx),
    windowsHide: true,
    stdio: [input === undefined ? 'inherit' : 'pipe', 'inherit', 'inherit'],
  });
  const stop = () => child.kill();
  process.once('SIGINT', stop);
  process.once('SIGTERM', stop);
  if (input !== undefined) {
    child.stdin.on('error', () => {});
    child.stdin.end(input);
  }
  try {
    await new Promise((resolvePromise, reject) => {
      child.once('error', reject);
      child.once('close', (code) =>
        code === 0 ? resolvePromise() : reject(new Error(`Basic Memory exited ${code}.`)),
      );
    });
  } finally {
    process.removeListener('SIGINT', stop);
    process.removeListener('SIGTERM', stop);
  }
}

async function checkMcp(ctx) {
  const child = spawn(ctx.python, [guardedRuntime, 'mcp'], {
    cwd: ctx.root,
    env: environment(ctx),
    windowsHide: true,
    stdio: ['pipe', 'pipe', 'pipe'],
  });
  let sequence = 0;
  let buffer = '';
  let stderr = '';
  const pending = new Map();
  child.stderr.on('data', (chunk) => {
    stderr = (stderr + chunk).slice(-4000);
  });
  child.stdin.on('error', () => {});
  child.stdout.on('data', (chunk) => {
    buffer += chunk;
    let end;
    while ((end = buffer.indexOf('\n')) >= 0) {
      const line = buffer.slice(0, end);
      buffer = buffer.slice(end + 1);
      try {
        const message = JSON.parse(line);
        pending.get(message.id)?.(message);
      } catch {
        /* Ignore non-protocol logging; initialization still must succeed. */
      }
    }
  });
  function call(method, params) {
    const id = ++sequence;
    return new Promise((resolvePromise, reject) => {
      const timer = setTimeout(() => {
        pending.delete(id);
        reject(new Error(`MCP ${method} timed out. ${stderr}`));
      }, 90000);
      pending.set(id, (message) => {
        pending.delete(id);
        clearTimeout(timer);
        if (message.error || message.result?.isError) reject(new Error(JSON.stringify(message)));
        else resolvePromise(message.result);
      });
      child.stdin.write(`${JSON.stringify({ jsonrpc: '2.0', id, method, params })}\n`);
    });
  }
  const closed = new Promise((resolvePromise) => child.once('close', resolvePromise));
  try {
    await call('initialize', {
      protocolVersion: '2024-11-05',
      capabilities: {},
      clientInfo: { name: 'finevent-doctor', version: '1' },
    });
    child.stdin.write(
      `${JSON.stringify({ jsonrpc: '2.0', method: 'notifications/initialized' })}\n`,
    );
    const inventory = await call('tools/list', {});
    const projects = await call('tools/call', {
      name: 'list_memory_projects',
      arguments: { output_format: 'json' },
    });
    if (!JSON.stringify(projects).includes(ctx.project))
      throw new Error('MCP did not expose the assigned project.');
    return inventory.tools.length;
  } finally {
    child.stdin.end();
    const timer = setTimeout(() => child.kill(), 5000);
    await closed;
    clearTimeout(timer);
  }
}

export function portableCheckpointBrief(brief) {
  // Basic Memory 0.24.1 puts its private checkpoint request on the first line.
  // Keep the project context and replace the entire request, including its payload.
  return brief.replace(
    /^Basic Memory checkpoint required after compaction\.[^\r\n]*/,
    'Use the project bm-checkpoint skill to save this handoff. Include repository-relative paths and Git references. Omit workstation paths, OS usernames, hostnames, and chat or turn IDs from metadata, prose, and links.',
  );
}

function sessionStart(ctx, input) {
  const brief = runSync(ctx, ctx.executable, ['hook', 'session-start', '--harness', 'codex'], {
    input,
    stdio: ['pipe', 'pipe', 'pipe'],
    timeout: 30000,
  });
  return portableCheckpointBrief(brief);
}

async function doctor(ctx) {
  requireRuntime(ctx);
  const toolCount = await checkMcp(ctx);
  const input = JSON.stringify({
    hook_event_name: 'SessionStart',
    session_id: `doctor-${randomUUID()}`,
    cwd: ctx.root,
    source: 'startup',
  });
  const output = sessionStart(ctx, input);
  // The official Codex CLI hook emits Markdown on stdout, not a JSON envelope.
  if (!output.includes('# Basic Memory') || !output.includes(ctx.project))
    throw new Error('SessionStart returned no project context.');
  console.log(
    JSON.stringify(
      {
        version,
        worktree: ctx.root,
        project: ctx.project,
        knowledge: ctx.notes,
        settings: ctx.settings,
        toolCount,
        sessionStartContext: true,
      },
      null,
      2,
    ),
  );
}

export async function main(args = process.argv.slice(2)) {
  const [command, ...rest] = args;
  if (command === 'hook') {
    if (!['session-start', 'pre-compact'].includes(rest[0])) throw new Error('Unsupported hook.');
    let input = '';
    for await (const chunk of process.stdin) input += chunk;
    const payload = JSON.parse(input);
    const ctx = bind(payload.cwd ?? process.cwd());
    requireRuntime(ctx);
    if (rest[0] === 'session-start') process.stdout.write(sessionStart(ctx, input));
    else await relay(ctx, ctx.executable, ['hook', rest[0], '--harness', 'codex'], input);
    return;
  }
  const ctx = bind(process.cwd());
  if (command === 'bind') console.log(JSON.stringify({ ...ctx, version }, null, 2));
  else if (command === 'setup') {
    const uv = findUv(ctx);
    if (!existsSync(ctx.python)) runSync(ctx, uv, ['venv', '--python', '3.12', ctx.runtime]);
    if (installedVersion(ctx) !== version)
      runSync(ctx, uv, ['pip', 'install', '--python', ctx.python, `basic-memory==${version}`]);
    requireRuntime(ctx);
    runSync(ctx, ctx.python, [guardedRuntime, 'project', 'index', ctx.project]);
    await doctor(ctx);
  } else if (command === 'doctor') await doctor(ctx);
  else if (command === 'mcp' || command === 'cli') {
    requireRuntime(ctx);
    await relay(ctx, ctx.python, [guardedRuntime, ...(command === 'mcp' ? ['mcp'] : rest)]);
  } else throw new Error('Usage: knowledge.mjs setup | doctor | mcp | cli <args>');
}

if (process.argv[1] && import.meta.url === pathToFileURL(resolve(process.argv[1])).href) {
  main().catch((error) => {
    console.error(error.message);
    process.exitCode = 1;
  });
}
