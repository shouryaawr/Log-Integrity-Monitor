'use client'
import { riskColor } from '@/lib/api'

interface Props {
  score: number
  riskLevel: string
  size?: number
}

export function ForensicScoreRing({ score, riskLevel, size = 160 }: Props) {
  const radius = (size - 20) / 2
  const circumference = 2 * Math.PI * radius
  const filled = (score / 100) * circumference
  const dashoffset = circumference - filled
  const color = riskColor(riskLevel)

  return (
    <div className="flex flex-col items-center gap-3">
      <svg width={size} height={size} style={{ transform: 'rotate(-90deg)' }}>
        {/* Glow filter */}
        <defs>
          <filter id="glow">
            <feGaussianBlur stdDeviation="3" result="coloredBlur" />
            <feMerge>
              <feMergeNode in="coloredBlur" />
              <feMergeNode in="SourceGraphic" />
            </feMerge>
          </filter>
        </defs>

        {/* Track */}
        <circle
          cx={size / 2} cy={size / 2} r={radius}
          fill="none"
          stroke="rgba(255,255,255,0.07)"
          strokeWidth="10"
        />

        {/* Value arc */}
        <circle
          cx={size / 2} cy={size / 2} r={radius}
          fill="none"
          stroke={color}
          strokeWidth="10"
          strokeLinecap="round"
          strokeDasharray={circumference}
          strokeDashoffset={dashoffset}
          filter="url(#glow)"
          style={{ transition: 'stroke-dashoffset 1.2s cubic-bezier(0.4,0,0.2,1)' }}
        />

        {/* Score text — counter-rotated */}
        <text
          x={size / 2} y={size / 2 + 2}
          textAnchor="middle"
          dominantBaseline="middle"
          fill={color}
          fontSize={size * 0.22}
          fontFamily="Syne, sans-serif"
          fontWeight="800"
          style={{ transform: `rotate(90deg)`, transformOrigin: `${size / 2}px ${size / 2}px` }}
        >
          {score}
        </text>
      </svg>

      <div className="text-center">
        <div
          className="text-xs font-mono px-3 py-1 rounded-full"
          style={{
            background: `${color}18`,
            border: `1px solid ${color}40`,
            color,
          }}
        >
          {riskLevel} RISK
        </div>
      </div>
    </div>
  )
}
