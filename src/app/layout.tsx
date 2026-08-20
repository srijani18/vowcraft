import type { Metadata, Viewport } from 'next'
import './globals.css'
import { ToastProvider } from '@/components/ui/Toast'
import { THEME_INIT_SCRIPT } from '@/components/ui/ThemeToggle'

export const metadata: Metadata = {
  title: 'Vowcraft — voice to action',
  description:
    'Turn conversations into approved, executed outcomes: transcription, action-item extraction, human-in-the-loop execution.',
}

export const viewport: Viewport = {
  // Matches the light and dark page backgrounds, so the browser chrome on mobile
  // does not sit at odds with the palette.
  themeColor: [
    { media: '(prefers-color-scheme: light)', color: '#EEF4F5' },
    { media: '(prefers-color-scheme: dark)', color: '#0E2126' },
  ],
  width: 'device-width',
  initialScale: 1,
}

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" suppressHydrationWarning>
      <head>
        {/*
          Applies the stored theme before first paint. Inline and synchronous on
          purpose — a deferred script would let the default theme paint and flash.
        */}
        <script dangerouslySetInnerHTML={{ __html: THEME_INIT_SCRIPT }} />
        {/*
          Fonts are linked at runtime rather than fetched by next/font at build
          time: the Docker image then builds without network access, and an offline
          viewer falls back to the system stack instead of a failed build.
        */}
        <link rel="preconnect" href="https://fonts.googleapis.com" />
        <link rel="preconnect" href="https://fonts.gstatic.com" crossOrigin="anonymous" />
        <link
          rel="stylesheet"
          href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500&display=swap"
        />
      </head>
      <body>
        {/* Skip link: the module rail is long, and keyboard users should not have
            to tab through every module to reach the page. */}
        <a
          href="#main"
          className="sr-only focus:not-sr-only focus:absolute focus:left-4 focus:top-4 focus:z-[60] focus:rounded-lg focus:bg-accent-fill focus:px-4 focus:py-2 focus:text-sm focus:font-semibold focus:text-accent-on"
        >
          Skip to content
        </a>
        <ToastProvider>{children}</ToastProvider>
      </body>
    </html>
  )
}
