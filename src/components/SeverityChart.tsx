'use client'
import { PieChart, Pie, Cell, Tooltip, ResponsiveContainer, Legend } from 'recharts'

interface Props {
  high:   number
  medium: number
  low:    number
}

// Align with CSS variable values from globals.css
const SEVERITY_COLORS: Record<string, string> = {
  HIGH:   '#ff4d6d',
  MEDIUM: '#ffa94d',
  LOW:    '#69db7c',
}

interface TooltipPayloadItem {
  name:    string
  value:   number
  payload: { fill: string }
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
        <span style={{ color: 'rgba(255,255,255,0.85)' }}>{item.value}</span>
      </div>
    )
  }
  return null
}

export function SeverityChart({ high, medium, low }: Props) {
  const data = [
    { name: 'HIGH',   value: high,   fill: SEVERITY_COLORS.HIGH   },
    { name: 'MEDIUM', value: medium, fill: SEVERITY_COLORS.MEDIUM },
    { name: 'LOW',    value: low,    fill: SEVERITY_COLORS.LOW    },
  ].filter((d) => d.value > 0)

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
        >
          {data.map((entry) => (
            <Cell key={entry.name} fill={entry.fill} opacity={0.85} />
          ))}
        </Pie>
        <Tooltip content={<CustomTooltip />} />
        <Legend
          iconType="circle"
          iconSize={8}
          formatter={(value: string) => (
            <span style={{ color: 'rgba(255,255,255,0.6)', fontSize: 11, fontFamily: 'JetBrains Mono' }}>
              {value}
            </span>
          )}
        />
      </PieChart>
    </ResponsiveContainer>
  )
}
