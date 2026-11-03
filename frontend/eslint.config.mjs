/**
 * eslint.config.js — gate statico minimo (audit frontend 03/11/2026).
 *
 * Volutamente SOLO regole che individuano bug certi, non stile:
 *  - no-undef / react/jsx-no-undef: identificatori o componenti usati senza
 *    import (es. humanizeError in useInlinePreview: spinner infinito).
 *  - react-hooks/rules-of-hooks: hook dopo un return anticipato (es.
 *    AllowedGroupsSelector, DynamicConfig: crash "Rendered fewer hooks").
 * Gira in CI con `npm run lint`.
 */
import globals from 'globals';
import react from 'eslint-plugin-react';
import reactHooks from 'eslint-plugin-react-hooks';

export default [
  { ignores: ['dist/**', 'node_modules/**', 'public/**', 'e2e/**', 'playwright-report/**', 'test-results/**'] },
  {
    files: ['src/**/*.{js,jsx}'],
    languageOptions: {
      ecmaVersion: 'latest',
      sourceType: 'module',
      parserOptions: { ecmaFeatures: { jsx: true } },
      globals: { ...globals.browser, ...globals.es2021 },
    },
    plugins: { react, 'react-hooks': reactHooks },
    settings: { react: { version: 'detect' } },
    linterOptions: { reportUnusedDisableDirectives: 'off' },
    rules: {
      'no-undef': 'error',
      'react/jsx-no-undef': 'error',
      'react/jsx-uses-vars': 'error',
      'react-hooks/rules-of-hooks': 'error',
    },
  },
];
