// Wings 3.0 Frontend ESLint flat config
// 仅做代码质量门禁，不改动业务代码。
// 首轮 bootstrap：把常见"噪声"规则降级为 warn，避免整库红叉淹没真实问题。
import js from "@eslint/js";
import tseslint from "typescript-eslint";
import pluginVue from "eslint-plugin-vue";
import globals from "globals";

export default [
  { ignores: ["dist/**", "node_modules/**"] },
  js.configs.recommended,
  ...tseslint.configs.recommended,
  // 原配置写 "flat/vue3-recommended"，但该 key 在 eslint-plugin-vue@9 不存在
  // （仅 legacy .eslintrc 形态才有 vue3-recommended）。vue3 的 flat 等价项是
  // "flat/recommended"。修正此 key 后 lint 才真正运行，而非在 spread 时崩溃。
  ...pluginVue.configs["flat/recommended"],
  {
    // 为 .vue 的 <script lang="ts"> 块指定 TS parser
    files: ["**/*.vue"],
    languageOptions: {
      parserOptions: {
        parser: tseslint.parser,
      },
    },
  },
  {
    languageOptions: {
      globals: {
        ...globals.browser,
        ...globals.node,
      },
    },
    rules: {
      // 首轮：未使用变量降级为 warn，不阻断 CI
      "no-unused-vars": "off",
      "@typescript-eslint/no-unused-vars": "warn",
      // 单文件组件命名风格：项目内大量组件为单词名，先放开
      "vue/multi-word-component-names": "off",
      "vue/no-v-html": "off",
    },
  },
];
