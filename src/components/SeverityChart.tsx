'use client'
import { useState, useCallback } from 'react'
import { PieChart, Pie, Cell, Tooltip, ResponsiveContainer } from 'recharts'

interface Props {
  high:   number
  medium: number
  low:    number
}

// Hex values for recharts SVG fills (CSS vars are not supported in SVG attributes)
const SEVERITY_HEX: Record<string, string> = {
  HIGH:   '#ff4d6d', // --color-high
  MEDIUM: '#ffa94d', // --color-medium
  LOW:    '#69db7c', // --color-low
}

// CSS-var color references for non-SVG elements (single source of truth from globals.css)
const SEVERITY_VAR: Record<string, string> = {
  HIGH:   'var(--color-high)',
  MEDIUM: 'var(--color-medium)',
  LOW:    'var(--color-low)',
}

interface ChartEntry {
  name:  string
  value: number
  fill:  string
  color: string
  pct:   string
}

interface TooltipPayloadItem {
  name:    string
  value:   number
  payload: ChartEntry
}

// ── Custom glassmorphic tooltip ───────────────────────────────────────────────
const CustomTooltip = ({
  active,
  payload,
}: {
  active?: boolean
  payload?: TooltipPayloadItem[]
}) => {
  if (!active || !payload || payload.length === 0) return null
  const item = payload[0].payload
  return (
    <div
      style={{
        background:    'rgba(10, 14, 26, 0.85)',
        border:        '1px solid rgba(255,255,255,0.10)',
        backdropFilter: 'blur(20px)',
        WebkitBackdropFilter: 'blur(20px)',
        borderRadius:  10,
        padding:       '8px 12px',
      }}
    >
      <span className="text-xs font-mono font-semibold" style={{ color: item.color }}>
        {item.name}
      </span>
      <span className="text-xs font-mono ml-2" style={{ color: 'rgba(255,255,255,0.85)' }}>
        {item.pct}
      </span>
      <span className="text-xs font-mono ml-1" style={{ color: 'rgba(255,255,255,0.35)' }}>
        ({item.value} gap{item.value !== 1 ? 's' : ''})
      </span>
    </div>
  )
}

// ── Main component ────────────────────────────────────────────────────────────
export function SeverityChart({ high, medium, low }: Props) {
  const total = high + medium + low

  // Safe percentage helper — satisfies requirement #3 (no NaN%, no blank)
  const pct = (value: number) =>
    total > 0 ? `${((value / total) * 100).toFixed(1)}%` : '0%'

  const data: ChartEntry[] = [
    { name: 'HIGH',   value: high,   fill: SEVERITY_HEX.HIGH,   color: SEVERITY_VAR.HIGH,   pct: pct(high)   },
    { name: 'MEDIUM', value: medium, fill: SEVERITY_HEX.MEDIUM, color: SEVERITY_VAR.MEDIUM, pct: pct(medium) },
    { name: 'LOW',    value: low,    fill: SEVERITY_HEX.LOW,    color: SEVERITY_VAR.LOW,     pct: pct(low)    },
  ].filter((d) => d.value > 0)

  // ── Animation sync state (requirement #1) ──────────────────────────────────
  // Labels start hidden; they fade in only after the Recharts animation ends.
  const [labelsVisible, setLabelsVisible] = useState(false)
  const handleAnimationEnd = useCallback(() => setLabelsVisible(true), [])

  if (data.length === 0) {
    return (
      <div
        className="flex items-center justify-center h-32 text-sm font-mono"
        style={{ color: 'rgba(255,255,255,0.3)' }}
        role="status"
        aria-label="No gaps to chart"
      >
        No gaps to chart
      </div>
    )
  }

  return (
    <div className="flex flex-col gap-4">
      {/* ── Donut chart — static labels removed, tooltip provides the data ── */}
      <ResponsiveContainer width="100%" height={200}>
        <PieChart>
          <Pie
            data={data}
            cx="50%"
            cy="50%"
            innerRadius={55}
            outerRadius={80}
            dataKey="value"
            paddingAngle={3}
            isAnimationActive={true}
            animationBegin={0}
            animationDuration={900}
            onAnimationEnd={handleAnimationEnd}
          >
            {data.map((entry) => (
              <Cell key={entry.name} fill={entry.fill} opacity={0.9} />
            ))}
          </Pie>
          {/* Follow-the-cursor glassmorphic tooltip (requirement #2) */}
          <Tooltip
            content={<CustomTooltip />}
            cursor={false}
          />
        </PieChart>
      </ResponsiveContainer>

      {/* ── Percentage breakdown bars ────────────────────────────────────── */}
      {/* Labels fade in after the chart animation ends (requirements #1 & #4) */}
      <div
        className="flex flex-col gap-2"
        style={{
          opacity:    labelsVisible ? 1 : 0,
          transition: 'opacity 0.5s ease',
        }}
        aria-live="polite"
      >
        {data.map((item) => (
          <div key={item.name} className="flex flex-col gap-1">
            <div className="flex justify-between items-center">
              {/* Label color anchored to severity color (requirement #4) */}
              <span
                className="text-xs font-mono"
                style={{ color: item.color, opacity: 0.75 }}
              >
                {item.name}
              </span>
              <span
                className="text-xs font-mono font-semibold"
                style={{ color: item.color }}
              >
                {item.pct}
                <span
                  className="ml-1"
                  style={{ color: 'rgba(255,255,255,0.3)', fontWeight: 400 }}
                >
                  ({item.value})
                </span>
              </span>
            </div>
            <div className="h-1 rounded-full" style={{ background: 'rgba(255,255,255,0.07)' }}>
              <div
                className="h-1 rounded-full"
                style={{
                  width:      item.pct,
                  background: item.fill,
                  opacity:    0.85,
                  // Bar fills synchronised with label fade (slightly earlier so
                  // labels appear on top of a completed bar)
                  transition: 'width 0.9s ease',
                }}
              />
            </div>
          </div>
        ))}
      </div>
    </div>
  )
}
