// SPDX-License-Identifier: BUSL-1.1 - LoopSmith Insight. NOT MIT. See insight/LICENSE.
// issue #302 [E17.S1]. Flat config (ESLint 9+). `next lint` no longer exists in Next 16 (removed
// upstream, confirmed via `next --help`), so `lint` calls ESLint directly against this config.
// No `.js` extension on the import below: eslint-config-next's `exports` map has no
// `./core-web-vitals.js` entry, and adding one raises ERR_PACKAGE_PATH_NOT_EXPORTED.
import nextCoreWebVitals from "eslint-config-next/core-web-vitals";

export default nextCoreWebVitals;
