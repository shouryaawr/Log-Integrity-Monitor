'use client'
import { PieChart, Pie, Cell, Tooltip, ResponsiveContainer, Legend } from 'recharts'

interface Props {
  high:   number
  medium: number
  low:    number
}

// Aligned with CSS variable values from globals.css
const SEVERITY_COLORS: Record<string, string> = {
  HIGH:   'var(--color-high)',
  MEDIUM: 'var(--color-medium)',
  LOW:    'var(--color-low)',
}

// Solid hex values for recharts (CSS vars not supported inside SVG fill)
const SEVERITY_HEX: Record<string, string> = {
  HIGH:   '#ff4d6d',
  MEDIUM: '#ffa94d',
  LOW:    '#69db7c',
}

interface TooltipPayloadItem {
  name:    string
  value:   number
  payload: { fill: string; pct: string }
}

const CustomTooltip = ({
  active,
  payload,
}: {
  active?: boolean
  payload?: TooltipPayloadItem[]
}) => {
  if (active && payload && payload.length > 0) {
    const item = payload[0]
    return (
      <div className="glass-card px-3 py-2 text-xs font-mono">
        <span style={{ color: item.payload.fill }}>{item.name}: </span>
        <span style={{ color: 'rgba(255,255,255,0.85)' }}>
          {item.payload.pct}
          <span style={{ color: 'rgba(255,255,255,0.4)', marginLeft: 4 }}>
            ({item.value} gap{item.value !== 1 ? 's' : ''})
          </span>
        </span>
      </div>
    )
  }
  return null
}

export function SeverityChart({ high, medium, low }: Props) {
  const total = high + medium + low

  const data = [
    { name: 'HIGH',   value: high,   fill: SEVERITY_HEX.HIGH,   color: SEVERITY_COLORS.HIGH },
    { name: 'MEDIUM', value: medium, fill: SEVERITY_HEX.MEDIUM, color: SEVERITY_COLORS.MEDIUM },
    { name: 'LOW',    value: low,    fill: SEVERITY_HEX.LOW,    color: SEVERITY_COLORS.LOW },
  ]
    .filter((d) => d.value > 0)
    .map((d) => ({
      ...d,
      pct: total > 0 ? `${((d.value / total) * 100).toFixed(1)}%` : '0%',
    }))

  if (data.length === 0) {
    return (
      <div
        className="flex items-center justify-center h-32 text-sm"
        style={{ color: 'rgba(255,255,255,0.3)' }}
        role="status"
      >
        No gaps to chart
      </div>
    )
  }

  return (
    <div className="flex flex-col gap-4">
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
            label={({ name, pct }) => `${pct}`}
            labelLine={false}
          >
            {data.map((entry) => (
              <Cell key={entry.name} fill={entry.fill} opacity={0.9} />
            ))}
          </Pie>
          <Tooltip content={<CustomTooltip />} />
          <Legend
            iconType="circle"
            iconSize={8}
            formatter={(value: string, _entry: unknown, index: number) => {
              const item = data[index]
              return (
                <span style={{ color: 'rgba(255,255,255,0.6)', fontSize: 11, fontFamily: 'JetBrains Mono' }}>
                  {value}
                  <span style={{ color: 'rgba(255,255,255,0.35)', marginLeft: 4 }}>
                    {item?.pct}
                  </span>
                </span>
              )
            }}
          />
        </PieChart>
      </ResponsiveContainer>

      {/* Percentage bar breakdown */}
      <div className="flex flex-col gap-2">
        {data.map((item) => (
          <div key={item.name} className="flex flex-col gap-1">
            <div className="flex justify-between items-center">
              <span className="text-xs font-mono" style={{ color: 'rgba(255,255,255,0.45)' }}>
                {item.name}
              </span>
              <span className="text-xs font-mono font-semibold" style={{ color: item.color }}>
                {item.pct}
                <span className="ml-1" style={{ color: 'rgba(255,255,255,0.3)', fontWeight: 400 }}>
                  ({item.value})
                </span>
              </span>
            </div>
            <div className="h-1 rounded-full" style={{ background: 'rgba(255,255,255,0.07)' }}>
              <div
                className="h-1 rounded-full transition-all duration-700"
                style={{
                  width: item.pct,
                  background: item.fill,
                  opacity: 0.85,
                }}
              />
            </div>
          </div>
        ))}
      </div>
    </div>
  )
}
