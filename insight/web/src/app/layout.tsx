// SPDX-License-Identifier: BUSL-1.1 - LoopSmith Insight. NOT MIT. See insight/LICENSE.
// issue #302 [E17.S1]. Minimal App Router root layout -- scaffold only, no design tokens (E17.S2)
// or auth (out of scope, per the issue).
import type { Metadata } from "next";

import "./globals.css";

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
      <body>{children}</body>
    </html>
  );
}
