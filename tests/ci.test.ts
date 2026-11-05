import { spawnSync } from 'node:child_process';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { devNull } from 'node:os';

import { describe, expect, it } from 'vitest';

interface Step {
  id?: string;
  run?: string;
}
const yaml = createRequire(import.meta.url)('js-yaml') as {
  load: (source: string) => { jobs: { checks: { steps: Step[] } } };
};
const workflow = yaml.load(
  readFileSync(new URL('../.github/workflows/ci.yml', import.meta.url), 'utf8'),
);
const steps = workflow.jobs.checks.steps;
const verifier = steps.find((step) => step.id === 'verify')?.run;
if (!verifier) throw new Error('Missing CI step verifier');
const script = verifier
  .replace(/^node --input-type=module <<'NODE'\r?\n/, '')
  .replace(/\r?\nNODE\s*$/, '');
const success = Object.fromEntries(
  steps
    .filter((step) => step.id && step.id !== 'verify')
    .map((step) => [step.id, { outcome: 'success' }]),
);

function verify(results: Record<string, { outcome: string }>, event = 'pull_request') {
  return spawnSync(process.execPath, ['--input-type=module', '-e', script], {
    encoding: 'utf8',
    env: {
      ...process.env,
      STEP_RESULTS: JSON.stringify(results),
      GITHUB_EVENT_NAME: event,
      GITHUB_OUTPUT: devNull,
    },
  });
}

describe('CI required-step gate', () => {
  it('accepts a completed PR run', () => {
    expect(verify(success).status).toBe(0);
  });

  it('requires commit validation only for PR runs', () => {
    const { commits: _commits, ...withoutCommits } = success;
    expect(verify(withoutCommits, 'push').status).toBe(0);
    expect(verify(withoutCommits).status).toBe(1);
  });

  it.each(['failure', 'skipped', 'cancelled'])('rejects %s in every required check', (outcome) => {
    for (const id of Object.keys(success)) {
      const result = verify({ ...success, [id]: { outcome } });
      expect({ id, status: result.status }).toEqual({ id, status: 1 });
      expect(result.stderr).toContain(id);
    }
  });

  it('rejects a missing E2E result', () => {
    const { e2e: _e2e, ...withoutE2E } = success;
    expect(verify(withoutE2E).status).toBe(1);
  });
});
