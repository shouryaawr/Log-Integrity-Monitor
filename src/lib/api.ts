// ── Base URL ──────────────────────────────────────────────────────────────────
// In production, NEXT_PUBLIC_API_URL can point to an absolute origin.
// In development the Next.js rewrite proxies /api/* → Flask :5000.
const BASE = process.env.NEXT_PUBLIC_API_URL
  ? `${process.env.NEXT_PUBLIC_API_URL}/api`
  : '/api'

/** Default ms before any fetch is hard-aborted. */
const DEFAULT_TIMEOUT_MS = 15_000

// ── Helpers ───────────────────────────────────────────────────────────────────

/** Wraps a fetch with an AbortController timeout. */
async function fetchWithTimeout(
  url: string,
  options: RequestInit = {},
  timeoutMs = DEFAULT_TIMEOUT_MS,
): Promise<Response> {
  const controller = new AbortController()
  const timerId = setTimeout(() => controller.abort(), timeoutMs)
  try {
    const res = await fetch(url, { ...options, signal: controller.signal })
    return res
  } catch (err: unknown) {
    if (err instanceof DOMException && err.name === 'AbortError') {
      throw new Error(`Request timed out after ${timeoutMs / 1000}s — is the Flask server running?`)
    }
    const msg = err instanceof Error ? err.message : String(err)
    // Network-level errors (ECONNREFUSED etc.) surface as TypeError in browsers
    if (msg.toLowerCase().includes('failed to fetch') || msg.toLowerCase().includes('networkerror')) {
      throw new Error('Cannot reach backend — is the Flask server running?')
    }
    throw err
  } finally {
    clearTimeout(timerId)
  }
}

/** Tries to parse error JSON from a non-OK response; falls back to status text. */
async function parseErrorResponse(res: Response, fallback: string): Promise<string> {
  try {
    const d = await res.json()
    return (d as { error?: string }).error ?? fallback
  } catch {
    return `${fallback} (HTTP ${res.status})`
  }
}

// ── Types ─────────────────────────────────────────────────────────────────────

export interface LogFile {
  name: string
  size: number
  modified: string
}

/**
 * Single temporal gap record produced by analyze_log().
 * Keys mirror the Flask backend dict keys exactly.
 */
export interface GapRecord {
  gap_number:       number
  severity:         'HIGH' | 'MEDIUM' | 'LOW'
  start_utc:        string
  end_utc:          string
  duration_seconds: number
  evidence_hash:    string
}

/**
 * Full analysis result — mirrors the Python dict from analyze_log().
 */
export interface AnalysisResult {
  gaps: GapRecord[]
  stats: {
    total_lines:      number
    malformed_lines:  number
    parseable_lines:  number
    gap_count:        number
    backward_jumps:   number
    tz_conversions:   number
    chain_hash:       string
  }
  forensic_score: {
    score:      number
    risk_level: 'LOW' | 'MODERATE' | 'HIGH' | 'CRITICAL' | 'UNKNOWN'
    factors:    Record<string, unknown>
  }
  summary: {
    total_gaps:                  number
    high_severity:               number
    medium_severity:             number
    low_severity:                number
    gap_density_per_1000_lines:  number
    assumed_tz:                  string | null
    summary_only:                boolean
  }
  performance: { execution_time_ms: number }
  _meta?: { filename: string; result_file: string }
}

export interface AnalyzeParams {
  filename:         string
  threshold?:       number
  high_threshold?:  number
  medium_threshold?: number
  max_gaps?:        number
  assume_tz?:       string
  summary_only?:    boolean
}

// ── API Functions ─────────────────────────────────────────────────────────────

export async function fetchLogs(): Promise<LogFile[]> {
  const res = await fetchWithTimeout(`${BASE}/logs`)
  if (!res.ok) {
    const msg = await parseErrorResponse(res, 'Failed to fetch log files')
    throw new Error(msg)
  }
  const data = await res.json() as { logs: LogFile[] }
  return data.logs
}

/**
 * Upload a log file to the Flask backend.
 *
 * Uses a chunked, streaming TextDecoder approach to avoid loading the entire
 * file into memory as a single base64 string — safe for large files.
 * The backend decodes with: base64.b64decode(urllib.parse.unquote(content))
 */
export async function uploadLog(file: File): Promise<void> {
  // Read file as text (handles most log encodings), strip control chars, encode.
  const text = await file.text()
  const cleaned = text.replace(/[\x00-\x08\x0B\x0C\x0E-\x1F\x7F]/g, '')
  // encodeURIComponent → btoa is safe for any Unicode text
  const encoded = btoa(encodeURIComponent(cleaned))

  const res = await fetchWithTimeout(
    `${BASE}/logs/upload`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ filename: file.name, content: encoded }),
    },
    60_000, // larger timeout for big files
  )

  if (!res.ok) {
    const msg = await parseErrorResponse(res, 'Upload failed')
    throw new Error(msg)
  }
}

export async function deleteLog(filename: string): Promise<void> {
  const res = await fetchWithTimeout(
    `${BASE}/logs/${encodeURIComponent(filename)}`,
    { method: 'DELETE' },
  )
  if (!res.ok) {
    const msg = await parseErrorResponse(res, 'Delete failed')
    throw new Error(msg)
  }
}

export async function analyzeLog(params: AnalyzeParams): Promise<AnalysisResult> {
  const res = await fetchWithTimeout(
    `${BASE}/analyze`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(params),
    },
    120_000, // analysis can take a while for large files
  )

  if (!res.ok) {
    const msg = await parseErrorResponse(res, 'Analysis failed')
    throw new Error(msg)
  }

  return res.json() as Promise<AnalysisResult>
}

export async function fetchResults(): Promise<{ name: string; size: number; modified: string }[]> {
  const res = await fetchWithTimeout(`${BASE}/results`)
  if (!res.ok) {
    const msg = await parseErrorResponse(res, 'Failed to fetch results')
    throw new Error(msg)
  }
  const data = await res.json() as { results: { name: string; size: number; modified: string }[] }
  return data.results
}

// ── Utility Helpers ───────────────────────────────────────────────────────────

export function formatDuration(seconds: number): string {
  const m = Math.floor(seconds / 60)
  const s = Math.floor(seconds % 60)
  if (m === 0) return `${s}s`
  const h = Math.floor(m / 60)
  if (h === 0) return `${m}m ${s}s`
  return `${h}h ${m % 60}m`
}

export function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / 1024 / 1024).toFixed(2)} MB`
}

export function riskColor(level: string): string {
  switch (level) {
    case 'LOW':      return 'var(--color-low)'
    case 'MODERATE': return 'var(--color-medium)'
    case 'HIGH':     return 'var(--color-high)'
    case 'CRITICAL': return 'var(--color-critical)'
    default:         return 'var(--color-unknown)'
  }
}

export function severityColor(severity: string): string {
  switch (severity) {
    case 'HIGH':   return 'var(--color-high)'
    case 'MEDIUM': return 'var(--color-medium)'
    case 'LOW':    return 'var(--color-low)'
    default:       return 'var(--color-unknown)'
  }
}
