// SPDX-License-Identifier: BUSL-1.1 - LoopSmith Insight. NOT MIT. See insight/LICENSE.
// issue #302 [E17.S1] / issue #305 [E17.S4]. Root layout: renders the app shell (Shell.tsx) around
// every route -- see Shell.tsx's own header and .sdlc/plans/305.md Decision 1 for why this single
// render point is what makes "every page uses the shell" hold structurally, not by convention.
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
