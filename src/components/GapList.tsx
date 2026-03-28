'use client'
import { GapRecord, formatDuration, severityColor } from '@/lib/api'

interface Props {
  gaps: GapRecord[]
}

export function GapList({ gaps }: Props) {
  if (gaps.length === 0) {
    return (
      <div className="glass-card-inset p-6 text-center">
        <div className="text-4xl mb-3">✓</div>
        <p className="text-sm" style={{ color: '#69db7c' }}>No suspicious gaps detected</p>
      </div>
    )
  }

  return (
    <div className="flex flex-col gap-3 max-h-[520px] overflow-y-auto pr-1">
      {gaps.map((gap) => {
        const color = severityColor(gap.severity)
        return (
          <div
            key={gap.gap_number}
            className="glass-card-inset p-4 rounded-xl transition-all duration-200 hover:border-opacity-30"
            style={{ borderLeft: `3px solid ${color}` }}
          >
            <div className="flex items-start justify-between gap-3">
              <div className="flex items-center gap-2">
                <span
                  className="text-xs font-mono font-semibold px-2 py-0.5 rounded"
                  style={{ background: `${color}18`, color, border: `1px solid ${color}35` }}
                >
                  #{gap.gap_number}
                </span>
                <span
                  className="text-xs font-mono font-bold px-2 py-0.5 rounded"
                  style={{ background: `${color}15`, color, border: `1px solid ${color}30` }}
                >
                  {gap.severity}
                </span>
              </div>
              <span className="text-sm font-display font-bold" style={{ color }}>
                {formatDuration(gap.duration_seconds)}
              </span>
            </div>

            <div className="mt-3 grid grid-cols-1 gap-1">
              <div className="flex gap-2 text-xs font-mono">
                <span style={{ color: 'rgba(255,255,255,0.35)', minWidth: 48 }}>START</span>
                <span style={{ color: 'rgba(255,255,255,0.7)' }}>{gap.start_utc}</span>
              </div>
              <div className="flex gap-2 text-xs font-mono">
                <span style={{ color: 'rgba(255,255,255,0.35)', minWidth: 48 }}>END</span>
                <span style={{ color: 'rgba(255,255,255,0.7)' }}>{gap.end_utc}</span>
              </div>
              <div className="flex gap-2 text-xs font-mono">
                <span style={{ color: 'rgba(255,255,255,0.35)', minWidth: 48 }}>HASH</span>
                <span style={{ color: 'rgba(255,255,255,0.4)' }} className="truncate">
                  {gap.evidence_hash}
                </span>
              </div>
            </div>
          </div>
        )
      })}
    </div>
  )
}
