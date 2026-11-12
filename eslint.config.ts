import js from '@eslint/js';
import pluginVitest from '@vitest/eslint-plugin';
import { withVueTs, vueTsConfigs } from '@vue/eslint-config-typescript';
import skipFormatting from 'eslint-config-prettier/flat';
import pluginOxlint from 'eslint-plugin-oxlint';
import pluginPlaywright from 'eslint-plugin-playwright';
import pluginVue from 'eslint-plugin-vue';
import { globalIgnores } from 'eslint/config';
import globals from 'globals';

export default withVueTs(
  { rootDir: import.meta.dirname },
  // 为 Vue、TypeScript 和 JavaScript 文件启用通用错误检查；专用规则由后续预设补充。
  {
    files: ['**/*.{vue,ts,mts,tsx,js,mjs,jsx}'],
    extends: [js.configs.recommended],
  },
  // 浏览器业务代码：声明浏览器全局变量；单元测试使用下方独立的测试环境。
  {
    files: ['apps/web/src/**/*.{vue,ts,mts,tsx}'],
    ignores: ['apps/web/src/**/__tests__/**'],
    languageOptions: { globals: globals.browser },
  },
  { files: ['apps/web/public/sw.js'], languageOptions: { globals: globals.serviceworker } },
  // Node.js 工程文件与测试脚本：声明 Node 全局变量；jsdom 单元测试会在下方叠加浏览器环境。
  {
    files: [
      '*.{ts,js,mjs}',
      'config/**/*.ts',
      'scripts/**/*.mjs',
      'tests/**/*.ts',
      'apps/web/*.config.ts',
      'apps/web/config/**/*.ts',
      'apps/web/scripts/**/*.mjs',
      'apps/web/src/**/__tests__/**/*.ts',
      'apps/web/tests/**/*.ts',
      'e2e/**/*.ts',
    ],
    languageOptions: { globals: globals.node },
  },

  // 全局排除构建产物、覆盖率、测试报告和临时验证目录，避免检查自动生成的内容。
  globalIgnores([
    '**/dist/**',
    '**/dist-ssr/**',
    '**/coverage/**',
    '**/playwright-report/**',
    '**/test-results/**',
    '**/.validation/**',
  ]),

  pluginVue.configs['flat/recommended'],
  vueTsConfigs.recommendedTypeChecked,

  // 为端到端测试用例启用 Playwright 专用规则，检查浏览器自动化测试的写法。
  {
    ...pluginPlaywright.configs['flat/recommended'],
    files: ['e2e/**/*.{test,spec}.{js,ts,jsx,tsx}'],
  },

  // 为前端单元测试启用 Vitest 专用规则。
  {
    ...pluginVitest.configs.recommended,
    files: [
      'apps/web/src/**/__tests__/**/*.ts',
      'apps/web/tests/**/*.test.ts',
      'tests/**/*.test.ts',
    ],
  },

  // 单元测试使用 jsdom：为测试及其辅助 TS 文件声明 Node 和浏览器全局变量，覆盖 tests 下非 .test.ts 文件。
  {
    files: ['apps/web/src/**/__tests__/**/*.ts', 'apps/web/tests/**/*.ts'],
    languageOptions: {
      globals: { ...globals.node, ...globals.browser },
    },
  },

  { linterOptions: { reportUnusedDisableDirectives: 'error' } },

  ...pluginOxlint.buildFromOxlintConfigFile('.oxlintrc.json'),

  skipFormatting,
);
