// SPDX-License-Identifier: BUSL-1.1 - LoopSmith Insight. NOT MIT. See insight/LICENSE.
// issue #302 [E17.S1], .sdlc/plans/302.md Decision (g).
//
// `output: "standalone"` traces the real dependency graph into `.next/standalone/`, including a
// pruned `node_modules/` copy, so the container runtime stage (insight/Dockerfile.web) can copy
// just that output and run `node server.js` without a second `npm ci` in the runtime image.
/** @type {import('next').NextConfig} */
const nextConfig = {
  output: "standalone",
};

export default nextConfig;
