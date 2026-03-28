'use client'
import { PieChart, Pie, Cell, Tooltip, ResponsiveContainer, Legend } from 'recharts'

interface Props {
  high: number
  medium: number
  low: number
}

const COLORS = ['#ff4d6d', '#ffa94d', '#69db7c']

const CustomTooltip = ({ active, payload }: any) => {
  if (active && payload && payload.length) {
    return (
      <div className="glass-card px-3 py-2 text-xs font-mono">
        <span style={{ color: payload[0].payload.fill }}>{payload[0].name}: </span>
        <span style={{ color: 'rgba(255,255,255,0.85)' }}>{payload[0].value}</span>
      </div>
    )
  }
  return null
}

export function SeverityChart({ high, medium, low }: Props) {
  const data = [
    { name: 'HIGH', value: high },
    { name: 'MEDIUM', value: medium },
    { name: 'LOW', value: low },
  ].filter((d) => d.value > 0)

  if (data.length === 0) {
    return (
      <div className="flex items-center justify-center h-32 text-sm" style={{ color: 'rgba(255,255,255,0.3)' }}>
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
          {data.map((entry, index) => (
            <Cell
              key={entry.name}
              fill={COLORS[['HIGH', 'MEDIUM', 'LOW'].indexOf(entry.name)]}
              opacity={0.85}
            />
          ))}
        </Pie>
        <Tooltip content={<CustomTooltip />} />
        <Legend
          iconType="circle"
          iconSize={8}
          formatter={(value) => (
            <span style={{ color: 'rgba(255,255,255,0.6)', fontSize: 11, fontFamily: 'JetBrains Mono' }}>
              {value}
            </span>
          )}
        />
      </PieChart>
    </ResponsiveContainer>
  )
}
