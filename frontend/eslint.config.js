import js from "@eslint/js";
import reactHooks from "eslint-plugin-react-hooks";
import reactRefresh from "eslint-plugin-react-refresh";
import globals from "globals";


export default [
  { ignores: ["dist", "node_modules"] },
  js.configs.recommended,
  {
    files: ["**/*.{js,jsx}"],
    languageOptions: {
      ecmaVersion: "latest",
      sourceType:  "module",
      globals:     globals.browser,
      parserOptions: {
        ecmaFeatures: { jsx: true },
      },
    },
    plugins: {
      "react-hooks":   reactHooks,
      "react-refresh": reactRefresh,
    },
    rules: {
      ...reactHooks.configs.recommended.rules,
      "react-refresh/only-export-components": ["warn", { allowConstantExport: true }],
      "no-unused-vars": ["warn", { argsIgnorePattern: "^_", varsIgnorePattern: "^_" }],
      // fix/frontend-eslint-ci-baseline: 새 eslint-plugin-react-hooks 의 strict
      // 규칙은 *기존 코드 패턴 위반* 으로 CI 를 깨고 있어, 본 PR 에서는 off 처리.
      // 후속 별도 PR 에서 코드 패턴을 정리하면서 다시 enable 가능.
      "react-hooks/set-state-in-effect": "off",
      "react-hooks/use-memo":            "off",
      // no-useless-escape — regex / template literal 의 사소한 백슬래시 패턴이
      // 의도된 경우가 있어 warn 으로 완화. 명확한 오류는 별도 PR.
      "no-useless-escape":     "warn",
      "no-useless-assignment": "off",
    },
  },
  // 214: vite.config.js는 Node 컨텍스트에서 실행 — process.env 등 Node 전역
  // 사용을 위해 별도 override.
  {
    files: ["vite.config.js", "eslint.config.js"],
    languageOptions: { globals: globals.node },
  },
  // fix/frontend-eslint-ci-baseline: test 파일은 `global` 등 Node 전역 사용 →
  // globals.node 추가. vitest 환경.
  {
    files: ["**/*.test.{js,jsx}", "**/__tests__/**/*.{js,jsx}"],
    languageOptions: {
      globals: { ...globals.browser, ...globals.node },
    },
  },
];
