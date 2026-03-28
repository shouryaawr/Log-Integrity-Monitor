import type { Metadata } from 'next'
import './globals.css'

export const metadata: Metadata = {
  title: 'Log Integrity Monitor',
  description: 'Streaming-based temporal anomaly analysis for log files',
}

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  )
}
