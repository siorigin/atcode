// Copyright (c) 2026 SiOrigin Co. Ltd.
// SPDX-License-Identifier: Apache-2.0

import type { Metadata } from "next";
import "./globals.css";
import { ThemeProvider } from "@/lib/theme-context";
import { I18nProvider } from "@/lib/i18n";
import { ToastProvider } from "@/components/Toast";
import { GlobalTaskMonitor } from "@/components/GlobalTaskMonitor";
import { GlobalChatProvider } from "@/components/GlobalChatProvider";
import { RepoViewerProvider } from "@/lib/repo-viewer-context";
import { FloatingRepoViewer } from "@/components/FloatingRepoViewer";
import { DockProvider } from "@/lib/dock-context";
import { DockableLayout } from "@/components/DockableLayout";
import { DockedPanels } from "@/components/DockedPanels";
import { ThemeErrorBoundary } from "@/components/ThemeErrorBoundary";

// Load Google Fonts using CDN mirror with performance optimization
const FONTS_MIRROR = process.env.FONTS_MIRROR || "https://fonts.loli.net";

export const metadata: Metadata = {
  title: "AtCode - Technical Documentation Interface",
  description: "Dual-pane technical documentation with synchronized code viewing",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en" suppressHydrationWarning>
      <head>
        <link
          rel="preconnect"
          href={FONTS_MIRROR}
        />
        <link
          rel="stylesheet"
          href={`${FONTS_MIRROR}/css2?family=Inter:wght@100..900&display=swap`}
        />
        <link
          rel="stylesheet"
          href={`${FONTS_MIRROR}/css2?family=JetBrains+Mono:wght@100..800&display=swap`}
        />
        <style
          dangerouslySetInnerHTML={{
            __html: `
              @font-face {
                font-family: 'Inter';
                font-weight: 100 900;
                font-style: normal;
                font-display: swap;
                src: local('Inter'), local('Inter_Fallback');
              }
              @font-face {
                font-family: 'JetBrains Mono';
                font-weight: 100 800;
                font-style: normal;
                font-display: swap;
                src: local('JetBrains Mono'), local('JetBrainsMono_Fallback');
              }
            `,
          }}
        />
      </head>
      <body className="antialiased">
        <ThemeProvider>
          <ThemeErrorBoundary>
          <I18nProvider>
            <ToastProvider>
              <DockProvider>
                <RepoViewerProvider>
                  <DockableLayout dockedContent={<DockedPanels />}>
                    {children}
                  </DockableLayout>
                  <FloatingRepoViewer />
                  <GlobalTaskMonitor position="bottom-right" />
                  <GlobalChatProvider />
                </RepoViewerProvider>
              </DockProvider>
            </ToastProvider>
          </I18nProvider>
          </ThemeErrorBoundary>
        </ThemeProvider>
      </body>
    </html>
  );
}
