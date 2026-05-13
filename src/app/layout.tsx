import type { Metadata } from 'next'
import './globals.css'

export const metadata: Metadata = {
  title: 'Log Integrity Monitor — Temporal Anomaly Analysis',
  description:
    'Streaming SHA-256 chain analysis for log files. Detect temporal gaps, backward jumps, and integrity anomalies in real time.',
  keywords: ['log analysis', 'forensics', 'SHA-256', 'temporal anomaly', 'integrity monitor'],
  authors: [{ name: 'Log Integrity Monitor' }],
  robots: { index: false, follow: false }, // internal tool — keep off search engines
}

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <head>
        <meta name="viewport" content="width=device-width, initial-scale=1" />
        <meta name="theme-color" content="#050810" />
      </head>
      <body suppressHydrationWarning>{children}</body>
    </html>
  )
}
