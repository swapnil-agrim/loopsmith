// SPDX-License-Identifier: BUSL-1.1 - LoopSmith Insight. NOT MIT. See insight/LICENSE.
// issue #302 [E17.S1]. Tailwind v4 is CSS-first: no tailwind.config.ts for defaults (design
// tokens are E17.S2, out of scope here) -- this is the one PostCSS wiring Tailwind v4 needs.
const config = {
  plugins: { "@tailwindcss/postcss": {} },
};

export default config;
