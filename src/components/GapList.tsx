'use client'
import { type GapRecord, formatDuration, severityColor } from '@/lib/api'

interface Props {
  gaps: GapRecord[]
}

export function GapList({ gaps }: Props) {
  if (gaps.length === 0) {
    return (
      <div className="glass-card-inset p-6 text-center" role="status">
        <div className="text-4xl mb-3" aria-hidden="true">✓</div>
        <p className="text-sm" style={{ color: 'var(--color-low)' }}>
          No suspicious gaps detected
        </p>
      </div>
    )
  }

  return (
    <ol
      className="flex flex-col gap-3 max-h-[520px] overflow-y-auto pr-1"
      aria-label="Detected temporal gaps"
    >
      {gaps.map((gap) => {
        const color = severityColor(gap.severity)
        return (
          <li
            key={gap.gap_number}
            className="glass-card-inset p-4 rounded-xl transition-all duration-200"
            style={{ borderLeft: `3px solid ${color}` }}
          >
            <div className="flex items-start justify-between gap-3">
              <div className="flex items-center gap-2">
                <span
                  className="text-xs font-mono font-semibold px-2 py-0.5 rounded"
                  style={{
                    background: `color-mix(in srgb, ${color} 12%, transparent)`,
                    color,
                    border: `1px solid color-mix(in srgb, ${color} 28%, transparent)`,
                  }}
                >
                  #{gap.gap_number}
                </span>
                <span
                  className="text-xs font-mono font-bold px-2 py-0.5 rounded"
                  style={{
                    background: `color-mix(in srgb, ${color} 10%, transparent)`,
                    color,
                    border: `1px solid color-mix(in srgb, ${color} 24%, transparent)`,
                  }}
                >
                  {gap.severity}
                </span>
              </div>
              <span className="text-sm font-display font-bold" style={{ color }}>
                {formatDuration(gap.duration_seconds)}
              </span>
            </div>

            <dl className="mt-3 grid grid-cols-1 gap-1">
              {([
                ['START', gap.start_utc],
                ['END',   gap.end_utc],
                ['HASH',  gap.evidence_hash],
              ] as const).map(([label, value]) => (
                <div key={label} className="flex gap-2 text-xs font-mono">
                  <dt style={{ color: 'rgba(255,255,255,0.35)', minWidth: 48 }}>{label}</dt>
                  <dd
                    style={{ color: 'rgba(255,255,255,0.7)' }}
                    className={label === 'HASH' ? 'truncate' : undefined}
                  >
                    {value}
                  </dd>
                </div>
              ))}
            </dl>
          </li>
        )
      })}
    </ol>
  )
}
