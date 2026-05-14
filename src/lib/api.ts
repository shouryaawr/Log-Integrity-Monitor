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

/** Paginated response from GET /api/logs */
export interface PaginatedLogsResponse {
  logs:        LogFile[]
  total:       number
  page:        number
  per_page:    number
  total_pages: number
}

/** Paginated response from GET /api/results */
export interface PaginatedResultsResponse {
  results:     LogFile[]
  total:       number
  page:        number
  per_page:    number
  total_pages: number
}

/** Health check response from GET /api/health */
export interface HealthResponse {
  status:   'ok' | 'degraded'
  service:  string
  checks: {
    logs_dir_accessible:    boolean
    results_dir_accessible: boolean
    logs_dir_writable:      boolean
    results_dir_writable:   boolean
  }
  uptime_seconds: number
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

/** Per-factor entry in forensic_score.factors */
export interface ForensicFactor {
  penalty: number
  reason:  string
}

/**
 * Full analysis result — mirrors the Python dict from analyze_log().
 * Includes all new fields added by the backend improvements.
 */
export interface AnalysisResult {
  /** Schema version — e.g. "1.1". Added by improvement 2. */
  schema_version: string

  gaps: GapRecord[]

  stats: {
    total_lines:          number
    malformed_lines:      number
    parseable_lines:      number
    gap_count:            number
    backward_jumps:       number
    tz_conversions:       number
    chain_hash:           string
    /** Hash algorithm used for the evidence chain — e.g. "sha256". */
    hash_algorithm:       string
    /** ISO timestamp of the first parsed log line (UTC). Null if no parseable lines. */
    log_start_utc:        string | null
    /** ISO timestamp of the last parsed log line (UTC). Null if no parseable lines. */
    log_end_utc:          string | null
    /** Total seconds from first to last log line. Null if < 2 parseable lines. */
    log_duration_seconds: number | null
    /** Parsed lines per second over the log's time span. Null if duration is 0. */
    line_rate_per_second: number | null
  }

  forensic_score: {
    score:      number
    risk_level: 'LOW' | 'MODERATE' | 'HIGH' | 'CRITICAL' | 'UNKNOWN'
    factors:    Record<string, ForensicFactor>
  }

  summary: {
    total_gaps:                  number
    high_severity:               number
    medium_severity:             number
    low_severity:                number
    gap_density_per_1000_lines:  number
    assumed_tz:                  string | null
    summary_only:                boolean
    /** Gap threshold (seconds) actually used for this analysis. */
    threshold_seconds:           number
    /** HIGH severity threshold (seconds) used. */
    high_threshold_seconds:      number
    /** MEDIUM severity threshold (seconds) used. */
    medium_threshold_seconds:    number
    /** Duration of the single largest gap. Null when no gaps found. */
    largest_gap_seconds:         number | null
    /** Mean gap duration across all detected gaps. Null when no gaps found. */
    average_gap_seconds:         number | null
  }

  performance: { execution_time_ms: number }

  _meta?: {
    filename:            string
    result_file:         string
    /** Correlation ID for this specific request — included in X-Request-ID header. */
    request_id:          string
    /** Size in bytes of the log file that was analysed. */
    file_size_bytes:     number
    /** True when the backend auto-promoted to summary_only due to file size. */
    forced_summary_only: boolean
  }
}

export interface AnalyzeParams {
  filename:          string
  threshold?:        number
  high_threshold?:   number
  medium_threshold?: number
  max_gaps?:         number
  assume_tz?:        string
  summary_only?:     boolean
}

// ── API Functions ─────────────────────────────────────────────────────────────

/** Fetch health status from the backend. */
export async function fetchHealth(): Promise<HealthResponse> {
  const res = await fetchWithTimeout(`${BASE}/health`, {}, 5_000)
  if (!res.ok) {
    // Server degraded but still responding — try to parse body anyway
    try {
      return await res.json() as HealthResponse
    } catch {
      throw new Error('Health check failed')
    }
  }
  return res.json() as Promise<HealthResponse>
}

/** Fetch a paginated list of stored log files. */
export async function fetchLogs(
  page = 1,
  perPage = 50,
): Promise<PaginatedLogsResponse> {
  const res = await fetchWithTimeout(
    `${BASE}/logs?page=${page}&per_page=${perPage}`,
  )
  if (!res.ok) {
    const msg = await parseErrorResponse(res, 'Failed to fetch log files')
    throw new Error(msg)
  }
  return res.json() as Promise<PaginatedLogsResponse>
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
    // Include the X-Request-ID in the error message if present, for diagnostics
    const reqId = res.headers.get('X-Request-ID')
    const msg = await parseErrorResponse(res, 'Analysis failed')
    throw new Error(reqId ? `${msg} [req: ${reqId}]` : msg)
  }

  return res.json() as Promise<AnalysisResult>
}

/** Fetch a paginated list of saved analysis result JSON files. */
export async function fetchResults(
  page = 1,
  perPage = 50,
): Promise<PaginatedResultsResponse> {
  const res = await fetchWithTimeout(
    `${BASE}/results?page=${page}&per_page=${perPage}`,
  )
  if (!res.ok) {
    const msg = await parseErrorResponse(res, 'Failed to fetch results')
    throw new Error(msg)
  }
  return res.json() as Promise<PaginatedResultsResponse>
}

// ── Utility Helpers ───────────────────────────────────────────────────────────

export function formatDuration(seconds: number): string {
  const total = Math.floor(seconds)
  const h = Math.floor(total / 3600)
  const m = Math.floor((total % 3600) / 60)
  const s = total % 60
  if (h > 0) return `${h}h ${m}m ${s}s`
  if (m > 0) return `${m}m ${s}s`
  return `${s}s`
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

/** Format a UTC ISO timestamp for human display (local timezone). */
export function formatUtcTimestamp(iso: string | null): string {
  if (!iso) return '—'
  try {
    return new Date(iso).toLocaleString(undefined, {
      dateStyle: 'medium',
      timeStyle: 'short',
    })
  } catch {
    return iso
  }
}
