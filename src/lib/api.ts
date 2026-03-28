const BASE = process.env.NEXT_PUBLIC_API_URL
  ? `${process.env.NEXT_PUBLIC_API_URL}/api`
  : '/api'

export interface LogFile {
  name: string
  size: number
  modified: string
}

export interface GapRecord {
  gap_number: number
  severity: 'HIGH' | 'MEDIUM' | 'LOW'
  start_utc: string
  end_utc: string
  duration_seconds: number
  evidence_hash: string
}

export interface AnalysisResult {
  gaps: GapRecord[]
  stats: {
    total_lines: number
    malformed_lines: number
    parseable_lines: number
    gap_count: number
    backward_jumps: number
    tz_conversions: number
    chain_hash: string
  }
  forensic_score: {
    score: number
    risk_level: 'LOW' | 'MODERATE' | 'HIGH' | 'CRITICAL' | 'UNKNOWN'
    factors: Record<string, any>
  }
  summary: {
    total_gaps: number
    high_severity: number
    medium_severity: number
    low_severity: number
    gap_density_per_1000_lines: number
    assumed_tz: string | null
    summary_only: boolean
  }
  performance: { execution_time_ms: number }
  _meta?: { filename: string; result_file: string }
}

export interface AnalyzeParams {
  filename: string
  threshold?: number
  high_threshold?: number
  medium_threshold?: number
  max_gaps?: number
  assume_tz?: string
  summary_only?: boolean
}

export async function fetchLogs(): Promise<LogFile[]> {
  const res = await fetch(`${BASE}/logs`)
  if (!res.ok) throw new Error('Failed to fetch log files')
  const data = await res.json()
  return data.logs
}

export async function uploadLog(file: File): Promise<void> {
  const form = new FormData()
  const cleanedContent = await file.text().then(text =>
    new Blob([text.replace(/[\x00-\x08\x0B\x0C\x0E-\x1F\x7F]/g, '')], { type: 'text/plain' })
  )
  form.append('file', cleanedContent, file.name)
  try {
    const res = await fetch(`${BASE}/logs/upload`, { method: 'POST', body: form })
    if (!res.ok) {
      let errMsg = 'Upload failed'
      try {
        const d = await res.json()
        errMsg = d.error || errMsg
      } catch {
        errMsg = `Upload failed with status ${res.status}`
      }
      throw new Error(errMsg)
    }
  } catch (err: any) {
    if (err.message.includes('fetch')) {
      throw new Error('Cannot reach backend — is the Flask server running?')
    }
    throw err
  }
}

export async function deleteLog(filename: string): Promise<void> {
  const res = await fetch(`${BASE}/logs/${encodeURIComponent(filename)}`, { method: 'DELETE' })
  if (!res.ok) throw new Error('Delete failed')
}

export async function analyzeLog(params: AnalyzeParams): Promise<AnalysisResult> {
  try {
    const res = await fetch(`${BASE}/analyze`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(params),
    })
    if (!res.ok) {
      let errMsg = 'Analysis failed'
      try {
        const d = await res.json()
        errMsg = d.error || errMsg
      } catch {
        errMsg = `Analysis failed with status ${res.status}`
      }
      throw new Error(errMsg)
    }
    return res.json()
  } catch (err: any) {
    if (err.message.includes('fetch')) {
      throw new Error('Cannot reach backend — is the Flask server running?')
    }
    throw err
  }
}

export async function fetchResults(): Promise<{ name: string; size: number; modified: string }[]> {
  const res = await fetch(`${BASE}/results`)
  if (!res.ok) throw new Error('Failed to fetch results')
  const data = await res.json()
  return data.results
}

export function formatDuration(seconds: number): string {
  const m = Math.floor(seconds / 60)
  const s = Math.floor(seconds % 60)
  if (m === 0) return `${s}s`
  return `${m}m ${s}s`
}

export function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / 1024 / 1024).toFixed(2)} MB`
}

export function riskColor(level: string): string {
  switch (level) {
    case 'LOW': return '#69db7c'
    case 'MODERATE': return '#ffa94d'
    case 'HIGH': return '#ff4d6d'
    case 'CRITICAL': return '#f03e3e'
    default: return 'rgba(255,255,255,0.4)'
  }
}

export function severityColor(severity: string): string {
  switch (severity) {
    case 'HIGH': return '#ff4d6d'
    case 'MEDIUM': return '#ffa94d'
    case 'LOW': return '#69db7c'
    default: return 'rgba(255,255,255,0.4)'
  }
}
