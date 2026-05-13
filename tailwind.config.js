/** @type {import('tailwindcss').Config} */
module.exports = {
  content: [
    './src/pages/**/*.{js,ts,jsx,tsx,mdx}',
    './src/components/**/*.{js,ts,jsx,tsx,mdx}',
    './src/app/**/*.{js,ts,jsx,tsx,mdx}',
  ],
  theme: {
    extend: {
      fontFamily: {
        mono:    ['JetBrains Mono', 'Fira Code', 'monospace'],
        display: ['Syne', 'sans-serif'],
        body:    ['DM Sans', 'sans-serif'],
      },
      // Severity palette references CSS variables so both Tailwind classes
      // and inline styles stay in sync with globals.css
      colors: {
        glass:         'rgba(255,255,255,0.05)',
        'glass-border':'rgba(255,255,255,0.12)',
        'glass-hover': 'rgba(255,255,255,0.10)',
        accent:        'var(--accent)',
        high:          'var(--color-high)',
        medium:        'var(--color-medium)',
        low:           'var(--color-low)',
        critical:      'var(--color-critical)',
      },
      backdropBlur: { glass: '20px' },
      boxShadow: {
        glass:      '0 8px 32px 0 rgba(0,0,0,0.37)',
        glow:       '0 0 20px rgba(116,192,252,0.25)',
        'glow-high':'0 0 20px rgba(255,77,109,0.30)',
        'glow-low': '0 0 20px rgba(105,219,124,0.30)',
      },
      animation: {
        'fade-in':  'fadeIn 0.4s ease forwards',
        'slide-up': 'slideUp 0.4s ease forwards',
        scan:       'scan 2s linear infinite',
      },
      keyframes: {
        fadeIn:  { from: { opacity: '0' }, to: { opacity: '1' } },
        slideUp: { from: { opacity: '0', transform: 'translateY(20px)' }, to: { opacity: '1', transform: 'translateY(0)' } },
        scan:    { '0%': { transform: 'translateY(0%)' }, '100%': { transform: 'translateY(100%)' } },
      },
    },
  },
  plugins: [],
}
