// SPDX-License-Identifier: BUSL-1.1 - LoopSmith Insight. NOT MIT. See insight/LICENSE.
// issue #302 [E17.S1] / issue #305 [E17.S4] / issue #307 [E18.S2]. Root layout: renders the app
// shell (Shell.tsx) around every route -- see Shell.tsx's own header and .sdlc/plans/305.md
// Decision 1 for why this single render point is what makes "every page uses the shell" hold
// structurally, not by convention. Auth (E18.S2: src/proxy.ts + auth.ts) is enforced OUTSIDE this
// file entirely -- nothing here changes for a protected route to exist, which is the point of
// doing it in the proxy rather than per-layout.
import type { Metadata } from "next";

import "./globals.css";
import { Shell } from "@/components/Shell";

export const metadata: Metadata = {
  title: "LoopSmith Insight",
  description: "SDLC analytics for LoopSmith.",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en">
      <body>
        <Shell>{children}</Shell>
      </body>
    </html>
  );
}
