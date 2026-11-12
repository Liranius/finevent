import { defineConfig } from 'vitest/config';

export default defineConfig({
  test: {
    projects: [
      {
        test: {
          name: 'tools',
          environment: 'node',
          include: ['tests/**/*.test.ts'],
        },
      },
      'apps/web/vitest.config.ts',
    ],
  },
});
