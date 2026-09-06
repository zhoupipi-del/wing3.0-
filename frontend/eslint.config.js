// Wings 3.0 Frontend ESLint flat config
// 仅做代码质量门禁，不改动业务代码。
// 首轮 bootstrap：把常见"噪声"规则降级为 warn，避免整库红叉淹没真实问题。
import js from "@eslint/js";
import tseslint from "typescript-eslint";
import pluginVue from "eslint-plugin-vue";
import globals from "globals";

export default [
  {
    // 构建产物 / 临时文件一律忽略，绝不对压缩包做 lint
    // （dist945 / dist945audit 是历史构建残留，dist*/** 覆盖所有 dist 前缀目录）
    ignores: [
      "dist/**",
      "dist*/**",
      "node_modules/**",
      "vite.config.ts.timestamp-*.mjs",
    ],
  },
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
      // ── Ratchet（CI-STABILIZATION-R2）：历史债务降 warn，不阻断 CI；新增代码不得新增 error ──
      "no-unused-vars": "off",
      "@typescript-eslint/no-unused-vars": "warn",
      "@typescript-eslint/no-explicit-any": "warn",
      "@typescript-eslint/no-empty-object-type": "warn",
      "@typescript-eslint/no-unsafe-function-type": "warn",
      "@typescript-eslint/no-require-imports": "warn",
      "vue/no-unused-vars": "warn",
      "no-empty": ["warn", { "allowEmptyCatch": true }],
      // vue 排版类历史债务（不阻断 CI）
      "vue/multi-word-component-names": "off",
      "vue/no-v-html": "off",
      // correctness 类保持 error（js/ts/vue recommended 默认即为 error，此处不降级）：
      //   no-undef / no-unreachable / no-constant-condition / no-dupe-keys /
      //   no-duplicate-case / no-dupe-else-if / no-func-assign / no-case-declarations /
      //   no-setter-return / no-self-assign / no-sparse-arrays / no-control-regex /
      //   no-debugger / no-constant-binary-expression / vue/no-parsing-error / parser
    },
  },
  {
    // _verify_profile.mjs 用 require() 做一次性脚本引导，放行 no-require-imports
    files: ["**/_verify_profile.mjs"],
    rules: {
      "@typescript-eslint/no-require-imports": "off",
      "no-require-imports": "off",
    },
  },
];
