/** @type {import('next').NextConfig} */

// In production, NEXT_PUBLIC_API_URL is the absolute Flask origin.
// In development, fall back to the local Flask dev server.
const backendOrigin = process.env.NEXT_PUBLIC_API_URL || 'http://127.0.0.1:5000'

const nextConfig = {
  // Proxy /api/* → Flask backend (used in dev; in prod the frontend calls backendOrigin directly)
  async rewrites() {
    return [
      {
        source: '/api/:path*',
        destination: `${backendOrigin}/api/:path*`,
      },
    ]
  },

  // Security headers incl. strict CSP
  async headers() {
    const csp = [
      "default-src 'self'",
      "script-src 'self' 'unsafe-eval' 'unsafe-inline'",  // Next.js HMR needs unsafe-eval in dev
      "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com",
      "font-src 'self' https://fonts.gstatic.com",
      `connect-src 'self' ${backendOrigin}`,               // allow fetch() to the Flask backend
      "img-src 'self' data: blob:",
      "frame-ancestors 'none'",
      "base-uri 'self'",
      "form-action 'self'",
    ].join('; ')

    return [
      {
        source: '/(.*)',
        headers: [
          { key: 'Content-Security-Policy', value: csp },
          { key: 'X-Frame-Options', value: 'DENY' },
          { key: 'X-Content-Type-Options', value: 'nosniff' },
          { key: 'Referrer-Policy', value: 'strict-origin-when-cross-origin' },
          { key: 'Permissions-Policy', value: 'camera=(), microphone=(), geolocation=()' },
        ],
      },
    ]
  },
}

module.exports = nextConfig
