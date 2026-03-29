'use client'

import { useState, useRef, useCallback, useEffect } from 'react'
import {
import {
  fetchLogs, uploadLog, deleteLog, analyzeLog, downloadResult,
  formatBytes, formatDuration,
  type LogFile, type AnalysisResult, type AnalyzeParams,
} from '@/lib/api'
import { ForensicScoreRing } from '@/components/ForensicScoreRing'
import { GapList } from '@/components/GapList'
import { SeverityChart } from '@/components/SeverityChart'

// ── Icons ─────────────────────────────────────────────────────────────────────
const IconUpload   = () => <svg width="18" height="18" fill="none" stroke="currentColor" strokeWidth="2" viewBox="0 0 24 24"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="17 8 12 3 7 8"/><line x1="12" y1="3" x2="12" y2="15"/></svg>
const IconSearch   = () => <svg width="18" height="18" fill="none" stroke="currentColor" strokeWidth="2" viewBox="0 0 24 24"><circle cx="11" cy="11" r="8"/><path d="m21 21-4.35-4.35"/></svg>
const IconTrash    = () => <svg width="14" height="14" fill="none" stroke="currentColor" strokeWidth="2" viewBox="0 0 24 24"><polyline points="3 6 5 6 21 6"/><path d="M19 6l-1 14H6L5 6"/><path d="M10 11v6M14 11v6"/><path d="M9 6V4h6v2"/></svg>
const IconFile     = () => <svg width="16" height="16" fill="none" stroke="currentColor" strokeWidth="2" viewBox="0 0 24 24"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/></svg>
const IconShield   = () => <svg width="20" height="20" fill="none" stroke="currentColor" strokeWidth="1.5" viewBox="0 0 24 24"><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/></svg>
const IconActivity = () => <svg width="20" height="20" fill="none" stroke="currentColor" strokeWidth="1.5" viewBox="0 0 24 24"><polyline points="22 12 18 12 15 21 9 3 6 12 2 12"/></svg>
const IconClock    = () => <svg width="14" height="14" fill="none" stroke="currentColor" strokeWidth="2" viewBox="0 0 24 24"><circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/></svg>
const IconChevron  = ({ open }: { open: boolean }) => <svg width="16" height="16" fill="none" stroke="currentColor" strokeWidth="2" viewBox="0 0 24 24" style={{ transform: open ? 'rotate(180deg)' : 'rotate(0)', transition: 'transform 0.2s' }}><polyline points="6 9 12 15 18 9"/></svg>
const IconX        = () => <svg width="14" height="14" fill="none" stroke="currentColor" strokeWidth="2" viewBox="0 0 24 24"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>

// ── Stat card ─────────────────────────────────────────────────────────────────
function StatCard({ label, value, sub, color }: { label: string; value: string | number; sub?: string; color?: string }) {
  return (
    <div className="glass-card p-4">
      <p className="text-xs font-mono mb-1" style={{ color: 'rgba(255,255,255,0.4)' }}>{label}</p>
      <p className="text-2xl font-display font-bold" style={{ color: color || 'rgba(255,255,255,0.9)' }}>{value}</p>
      {sub && <p className="text-xs mt-1 font-mono" style={{ color: 'rgba(255,255,255,0.3)' }}>{sub}</p>}
    </div>
  )
}

// ── Settings row ──────────────────────────────────────────────────────────────
function SettingRow({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex flex-col gap-1">
      <label className="text-xs font-mono" style={{ color: 'rgba(255,255,255,0.45)' }}>{label}</label>
      {children}
    </div>
  )
}

// ── Spinner ───────────────────────────────────────────────────────────────────
function Spinner() {
  return (
    <svg className="animate-spin" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
      <path d="M21 12a9 9 0 1 1-6.22-8.56"/>
    </svg>
  )
}

// ════════════════════════════════════════════════════════════════════════════════
export default function Home() {
  const [logs, setLogs]               = useState<LogFile[]>([])
  const [logsLoading, setLogsLoading] = useState(false)
  const [uploading, setUploading]     = useState(false)
  const [selectedFile, setSelectedFile] = useState<string | null>(null)
  const [analyzing, setAnalyzing]     = useState(false)
  const [result, setResult]           = useState<AnalysisResult | null>(null)
  const [error, setError]             = useState<string | null>(null)
  const [dragOver, setDragOver]       = useState(false)
  const [showAdvanced, setShowAdvanced] = useState(false)
  const [activeTab, setActiveTab]     = useState<'gaps' | 'stats' | 'chart'>('gaps')

  // Settings
  const [threshold, setThreshold]           = useState('60')
  const [highThreshold, setHighThreshold]   = useState('3600')
  const [medThreshold, setMedThreshold]     = useState('600')
  const [maxGaps, setMaxGaps]               = useState('0')
  const [assumeTz, setAssumeTz]             = useState('')
  const [summaryOnly, setSummaryOnly]       = useState(false)

  const fileInputRef = useRef<HTMLInputElement>(null)

  const loadLogs = useCallback(async () => {
    setLogsLoading(true)
    try {
      const list = await fetchLogs()
      setLogs(list)
    } catch (e: any) {
      setError(e.message)
    } finally {
      setLogsLoading(false)
    }
  }, [])

  useEffect(() => { loadLogs() }, [loadLogs])

  // ── Upload ──────────────────────────────────────────────────────────────────
  const handleFiles = useCallback(async (files: FileList | null) => {
    if (!files || files.length === 0) return
    setUploading(true)
    setError(null)
    try {
      for (const file of Array.from(files)) {
        await uploadLog(file)
      }
      await loadLogs()
    } catch (e: any) {
      setError(e.message)
    } finally {
      setUploading(false)
    }
  }, [loadLogs])

  const onDrop = (e: React.DragEvent) => {
    e.preventDefault(); setDragOver(false)
    handleFiles(e.dataTransfer.files)
  }

  // ── Delete ──────────────────────────────────────────────────────────────────
  const handleDelete = async (name: string, e: React.MouseEvent) => {
    e.stopPropagation()
    try {
      await deleteLog(name)
      if (selectedFile === name) { setSelectedFile(null); setResult(null) }
      await loadLogs()
    } catch (e: any) { setError(e.message) }
  }

  // ── Analyze ─────────────────────────────────────────────────────────────────
  const handleAnalyze = async () => {
    if (!selectedFile) return
    setAnalyzing(true); setError(null); setResult(null)
    const params: AnalyzeParams = {
      filename:         selectedFile,
      threshold:        parseInt(threshold) || 60,
      max_gaps:         parseInt(maxGaps) || 0,
      summary_only:     summaryOnly,
    }
    if (highThreshold) params.high_threshold = parseInt(highThreshold)
    if (medThreshold)  params.medium_threshold = parseInt(medThreshold)
    if (assumeTz.trim()) params.assume_tz = assumeTz.trim()
    try {
      const res = await analyzeLog(params)
      setResult(res)
      setActiveTab('gaps')
    } catch (e: any) { setError(e.message) }
    finally { setAnalyzing(false) }
  }

  const riskLevel = result?.forensic_score.risk_level || ''

  // ── Render ───────────────────────────────────────────────────────────────────
  return (
    <main className="min-h-screen p-6 md:p-10" style={{ fontFamily: "'DM Sans', sans-serif" }}>

      {/* ── Header ── */}
      <header className="flex items-center justify-between mb-10">
        <div className="flex items-center gap-3">
          <div
            className="p-2.5 rounded-xl flex items-center justify-center"
            style={{ background: 'rgba(116,192,252,0.1)', border: '1px solid rgba(116,192,252,0.2)' }}
          >
            <IconShield />
          </div>
          <div>
            <h1 className="text-xl font-display font-bold tracking-tight" style={{ color: '#74c0fc', fontFamily: "'Syne', sans-serif" }}>
              Log Integrity Monitor
            </h1>
            <p className="text-xs font-mono" style={{ color: 'rgba(255,255,255,0.35)' }}>
              Temporal Anomaly Analysis Engine
            </p>
          </div>
        </div>

        <div className="flex items-center gap-2 text-xs font-mono" style={{ color: 'rgba(255,255,255,0.35)' }}>
          <span className="inline-block w-2 h-2 rounded-full bg-green-400 animate-pulse" />
          ONLINE
        </div>
      </header>

      {/* ── Error banner ── */}
      {error && (
        <div
          className="mb-6 flex items-center justify-between gap-3 px-4 py-3 rounded-xl text-sm font-mono"
          style={{ background: 'rgba(255,77,109,0.1)', border: '1px solid rgba(255,77,109,0.25)', color: '#ff4d6d' }}
        >
          <span>⚠ {error}</span>
          <button onClick={() => setError(null)}><IconX /></button>
        </div>
      )}

      <div className="grid grid-cols-1 xl:grid-cols-[380px_1fr] gap-6">

        {/* ══ LEFT PANEL ══════════════════════════════════════════════════════ */}
        <div className="flex flex-col gap-5">

          {/* Upload dropzone */}
          <div
            className="glass-card p-5 cursor-pointer relative overflow-hidden"
            style={{
              borderStyle: dragOver ? 'solid' : 'dashed',
              borderColor: dragOver ? 'rgba(116,192,252,0.5)' : 'rgba(255,255,255,0.1)',
              background: dragOver ? 'rgba(116,192,252,0.06)' : undefined,
              transition: 'all 0.2s',
            }}
            onDragOver={(e) => { e.preventDefault(); setDragOver(true) }}
            onDragLeave={() => setDragOver(false)}
            onDrop={onDrop}
            onClick={() => fileInputRef.current?.click()}
          >
            <input
              ref={fileInputRef}
              type="file"
              multiple
              accept=".log,.txt,.log.txt,text/*"
              className="hidden"
              onChange={(e) => handleFiles(e.target.files)}
            />

            <div className="flex flex-col items-center gap-2 py-3 text-center">
              {uploading
                ? <Spinner />
                : <div style={{ color: 'rgba(116,192,252,0.6)' }}><IconUpload /></div>
              }
              <p className="text-sm font-display" style={{ color: 'rgba(255,255,255,0.6)' }}>
                {uploading ? 'Uploading…' : 'Drop log files or click to browse'}
              </p>
              <p className="text-xs font-mono" style={{ color: 'rgba(255,255,255,0.25)' }}>
                .log · .txt · any text format
              </p>
            </div>
          </div>

          {/* File list */}
          <div className="glass-card p-4 flex flex-col gap-2">
            <div className="flex items-center justify-between mb-1">
              <h2 className="text-sm font-display font-semibold" style={{ color: 'rgba(255,255,255,0.7)', fontFamily: "'Syne', sans-serif" }}>
                Stored Logs
              </h2>
              <button
                onClick={loadLogs}
                className="text-xs font-mono px-2 py-1 rounded-lg"
                style={{ color: 'rgba(255,255,255,0.4)', background: 'rgba(255,255,255,0.05)', border: '1px solid rgba(255,255,255,0.08)' }}
              >
                {logsLoading ? '…' : 'Refresh'}
              </button>
            </div>

            {logs.length === 0 && !logsLoading && (
              <p className="text-xs font-mono py-3 text-center" style={{ color: 'rgba(255,255,255,0.25)' }}>
                No log files stored yet
              </p>
            )}

            <div className="flex flex-col gap-2 max-h-60 overflow-y-auto">
              {logs.map((log) => (
                <div
                  key={log.name}
                  className="flex items-center justify-between gap-2 px-3 py-2.5 rounded-xl cursor-pointer transition-all duration-150"
                  style={{
                    background: selectedFile === log.name ? 'rgba(116,192,252,0.1)' : 'rgba(255,255,255,0.03)',
                    border: selectedFile === log.name
                      ? '1px solid rgba(116,192,252,0.3)'
                      : '1px solid rgba(255,255,255,0.06)',
                  }}
                  onClick={() => { setSelectedFile(log.name); setResult(null); setError(null) }}
                >
                  <div className="flex items-center gap-2 min-w-0">
                    <span style={{ color: selectedFile === log.name ? '#74c0fc' : 'rgba(255,255,255,0.3)' }}>
                      <IconFile />
                    </span>
                    <div className="min-w-0">
                      <p className="text-xs font-mono truncate" style={{ color: 'rgba(255,255,255,0.8)' }}>{log.name}</p>
                      <p className="text-xs font-mono" style={{ color: 'rgba(255,255,255,0.3)' }}>{formatBytes(log.size)}</p>
                    </div>
                  </div>
                  <button
                    onClick={(e) => handleDelete(log.name, e)}
                    className="btn-danger flex-shrink-0 p-1.5 rounded-lg"
                    style={{ padding: '5px' }}
                  >
                    <IconTrash />
                  </button>
                </div>
              ))}
            </div>
          </div>

          {/* Analysis settings */}
          <div className="glass-card p-4 flex flex-col gap-4">
            <h2 className="text-sm font-display font-semibold" style={{ color: 'rgba(255,255,255,0.7)', fontFamily: "'Syne', sans-serif" }}>
              Analysis Settings
            </h2>

            <div className="grid grid-cols-2 gap-3">
              <SettingRow label="GAP THRESHOLD (s)">
                <input
                  type="number" min="1" value={threshold}
                  onChange={(e) => setThreshold(e.target.value)}
                  className="glass-input w-full px-3 py-2 text-sm font-mono"
                />
              </SettingRow>
              <SettingRow label="MAX GAPS (0=∞)">
                <input
                  type="number" min="0" value={maxGaps}
                  onChange={(e) => setMaxGaps(e.target.value)}
                  className="glass-input w-full px-3 py-2 text-sm font-mono"
                />
              </SettingRow>
            </div>

            {/* Advanced toggle */}
            <button
              className="flex items-center justify-between text-xs font-mono w-full"
              style={{ color: 'rgba(255,255,255,0.4)' }}
              onClick={() => setShowAdvanced(v => !v)}
            >
              <span>ADVANCED OPTIONS</span>
              <IconChevron open={showAdvanced} />
            </button>

            {showAdvanced && (
              <div className="flex flex-col gap-3 animate-fade-slide">
                <div className="grid grid-cols-2 gap-3">
                  <SettingRow label="HIGH THRESHOLD (s)">
                    <input
                      type="number" min="1" value={highThreshold}
                      onChange={(e) => setHighThreshold(e.target.value)}
                      className="glass-input w-full px-3 py-2 text-sm font-mono"
                    />
                  </SettingRow>
                  <SettingRow label="MEDIUM THRESHOLD (s)">
                    <input
                      type="number" min="1" value={medThreshold}
                      onChange={(e) => setMedThreshold(e.target.value)}
                      className="glass-input w-full px-3 py-2 text-sm font-mono"
                    />
                  </SettingRow>
                </div>
                <SettingRow label="ASSUME TIMEZONE">
                  <input
                    type="text" placeholder="e.g. IST, UTC+5:30, EST"
                    value={assumeTz}
                    onChange={(e) => setAssumeTz(e.target.value)}
                    className="glass-input w-full px-3 py-2 text-sm font-mono"
                  />
                </SettingRow>
                <label className="flex items-center gap-2 cursor-pointer">
                  <input
                    type="checkbox"
                    checked={summaryOnly}
                    onChange={(e) => setSummaryOnly(e.target.checked)}
                    className="rounded"
                    style={{ accentColor: '#74c0fc' }}
                  />
                  <span className="text-xs font-mono" style={{ color: 'rgba(255,255,255,0.5)' }}>
                    Summary only (suppress gap detail)
                  </span>
                </label>
              </div>
            )}

            <button
              className="btn-primary w-full flex items-center justify-center gap-2"
              disabled={!selectedFile || analyzing}
              onClick={handleAnalyze}
            >
              {analyzing ? <Spinner /> : <IconActivity />}
              {analyzing ? 'Analyzing…' : 'Run Analysis'}
            </button>
          </div>
        </div>

        {/* ══ RIGHT PANEL ═════════════════════════════════════════════════════ */}
        <div className="flex flex-col gap-5">

          {/* Empty / idle state */}
          {!result && !analyzing && (
            <div className="glass-card flex-1 flex flex-col items-center justify-center py-24 gap-4" style={{ minHeight: 400 }}>
              <div
                className="p-5 rounded-2xl"
                style={{ background: 'rgba(116,192,252,0.06)', border: '1px solid rgba(116,192,252,0.12)' }}
              >
                <IconShield />
              </div>
              <div className="text-center">
                <p className="font-display text-lg font-semibold" style={{ color: 'rgba(255,255,255,0.6)', fontFamily: "'Syne', sans-serif" }}>
                  No analysis yet
                </p>
                <p className="text-sm font-mono mt-1" style={{ color: 'rgba(255,255,255,0.25)' }}>
                  {selectedFile ? `Ready to scan "${selectedFile}"` : 'Upload or select a log file to begin'}
                </p>
              </div>
            </div>
          )}

          {/* Loading state */}
          {analyzing && (
            <div className="glass-card flex-1 flex flex-col items-center justify-center py-24 gap-6" style={{ minHeight: 400 }}>
              <div className="relative">
                {/* Outer ring pulse */}
                <div
                  className="absolute inset-0 rounded-full animate-ping"
                  style={{ background: 'rgba(116,192,252,0.08)', animationDuration: '1.5s' }}
                />
                <div
                  className="p-6 rounded-2xl relative"
                  style={{ background: 'rgba(116,192,252,0.08)', border: '1px solid rgba(116,192,252,0.2)' }}
                >
                  <IconActivity />
                </div>
              </div>
              <div className="text-center">
                <p className="font-display font-semibold" style={{ color: 'rgba(255,255,255,0.7)', fontFamily: "'Syne', sans-serif" }}>
                  Streaming analysis in progress…
                </p>
                <p className="text-xs font-mono mt-1" style={{ color: 'rgba(255,255,255,0.3)' }}>
                  Detecting temporal anomalies
                </p>
              </div>
            </div>
          )}

          {/* Results */}
          {result && !analyzing && (
            <div className="flex flex-col gap-5 animate-fade-slide">

              {/* Top metrics row */}
              <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
                <StatCard
                  label="TOTAL LINES"
                  value={result.stats.total_lines.toLocaleString()}
                  sub={`${result.stats.parseable_lines.toLocaleString()} parseable`}
                />
                <StatCard
                  label="GAPS DETECTED"
                  value={result.summary.total_gaps}
                  sub={`density: ${result.summary.gap_density_per_1000_lines}/1k lines`}
                  color={result.summary.total_gaps > 0 ? '#ffa94d' : '#69db7c'}
                />
                <StatCard
                  label="BACKWARD JUMPS"
                  value={result.stats.backward_jumps}
                  color={result.stats.backward_jumps > 0 ? '#ff4d6d' : undefined}
                />
                <StatCard
                  label="EXEC TIME"
                  value={`${result.performance.execution_time_ms.toFixed(1)}ms`}
                  sub="streaming pass"
                />
              </div>

              {/* Main content area */}
              <div className="grid grid-cols-1 lg:grid-cols-[1fr_260px] gap-5">

                {/* Left: tabs + content */}
                <div className="glass-card p-5 flex flex-col gap-4">

                  {/* Tab bar */}
                  <div className="flex gap-1 p-1 rounded-xl" style={{ background: 'rgba(0,0,0,0.2)' }}>
                    {(['gaps', 'stats', 'chart'] as const).map((tab) => (
                      <button
                        key={tab}
                        className="flex-1 py-1.5 rounded-lg text-xs font-mono font-semibold transition-all duration-200"
                        style={{
                          background: activeTab === tab ? 'rgba(116,192,252,0.15)' : 'transparent',
                          color: activeTab === tab ? '#74c0fc' : 'rgba(255,255,255,0.4)',
                          border: activeTab === tab ? '1px solid rgba(116,192,252,0.25)' : '1px solid transparent',
                        }}
                        onClick={() => setActiveTab(tab)}
                      >
                        {tab.toUpperCase()}
                      </button>
                    ))}
                  </div>

                  {/* Gaps tab */}
                  {activeTab === 'gaps' && (
                    <div>
                      <div className="flex items-center justify-between mb-3">
                        <h3 className="text-sm font-display font-semibold" style={{ fontFamily: "'Syne', sans-serif", color: 'rgba(255,255,255,0.7)' }}>
                          Detected Gaps
                        </h3>
                        <div className="flex gap-2">
                          <span className="badge-high px-2 py-0.5 rounded text-xs font-mono">
                            {result.summary.high_severity} HIGH
                          </span>
                          <span className="badge-medium px-2 py-0.5 rounded text-xs font-mono">
                            {result.summary.medium_severity} MED
                          </span>
                          <span className="badge-low px-2 py-0.5 rounded text-xs font-mono">
                            {result.summary.low_severity} LOW
                          </span>
                        </div>
                      </div>
                      <GapList gaps={result.gaps} />
                      {result.summary.summary_only && (
                        <p className="text-xs font-mono mt-3 text-center" style={{ color: 'rgba(255,255,255,0.3)' }}>
                          Summary-only mode — per-gap detail suppressed
                        </p>
                      )}
                    </div>
                  )}

                  {/* Stats tab */}
                  {activeTab === 'stats' && (
                    <div className="flex flex-col gap-3">
                      <h3 className="text-sm font-display font-semibold mb-1" style={{ fontFamily: "'Syne', sans-serif", color: 'rgba(255,255,255,0.7)' }}>
                        Processing Statistics
                      </h3>
                      {[
                        ['Total Lines',      result.stats.total_lines.toLocaleString()],
                        ['Parseable Lines',  result.stats.parseable_lines.toLocaleString()],
                        ['Malformed Lines',  result.stats.malformed_lines.toLocaleString()],
                        ['TZ Conversions',   result.stats.tz_conversions.toLocaleString()],
                        ['Backward Jumps',   result.stats.backward_jumps.toLocaleString()],
                        ['Gap Count',        result.stats.gap_count.toLocaleString()],
                        ['Assumed TZ',       result.summary.assumed_tz || 'UTC (naive)'],
                        ['Exec Time',        `${result.performance.execution_time_ms.toFixed(3)} ms`],
                      ].map(([label, value]) => (
                        <div key={label} className="flex justify-between items-center glass-card-inset px-3 py-2">
                          <span className="text-xs font-mono" style={{ color: 'rgba(255,255,255,0.4)' }}>{label}</span>
                          <span className="text-xs font-mono" style={{ color: 'rgba(255,255,255,0.8)' }}>{value}</span>
                        </div>
                      ))}
                      <div className="glass-card-inset px-3 py-2">
                        <p className="text-xs font-mono mb-1" style={{ color: 'rgba(255,255,255,0.4)' }}>CHAIN HASH</p>
                        <p className="text-xs font-mono break-all" style={{ color: 'rgba(116,192,252,0.7)' }}>
                          {result.stats.chain_hash}
                        </p>
                      </div>

                      {/* Forensic factors breakdown */}
                      {result.forensic_score.factors && typeof result.forensic_score.factors === 'object' && (
                        <div className="mt-2">
                          <p className="text-xs font-mono mb-2" style={{ color: 'rgba(255,255,255,0.4)' }}>FORENSIC PENALTY FACTORS</p>
                          <div className="flex flex-col gap-2">
                            {Object.entries(result.forensic_score.factors).map(([key, val]: [string, any]) => {
                              if (typeof val !== 'object' || val === null) return null
                              const penalty = val.penalty ?? 0
                              return (
                                <div key={key} className="glass-card-inset px-3 py-2">
                                  <div className="flex justify-between mb-1">
                                    <span className="text-xs font-mono capitalize" style={{ color: 'rgba(255,255,255,0.5)' }}>
                                      {key.replace(/_/g, ' ')}
                                    </span>
                                    <span className="text-xs font-mono" style={{ color: '#ffa94d' }}>-{penalty.toFixed(1)} pts</span>
                                  </div>
                                  <div className="h-1 rounded-full" style={{ background: 'rgba(255,255,255,0.07)' }}>
                                    <div
                                      className="h-1 rounded-full transition-all duration-700"
                                      style={{ width: `${Math.min(100, (penalty / 40) * 100)}%`, background: '#ffa94d' }}
                                    />
                                  </div>
                                </div>
                              )
                            })}
                          </div>
                        </div>
                      )}
                    </div>
                  )}

                  {/* Chart tab */}
                  {activeTab === 'chart' && (
                    <div>
                      <h3 className="text-sm font-display font-semibold mb-4" style={{ fontFamily: "'Syne', sans-serif", color: 'rgba(255,255,255,0.7)' }}>
                        Severity Distribution
                      </h3>
                      <SeverityChart
                        high={result.summary.high_severity}
                        medium={result.summary.medium_severity}
                        low={result.summary.low_severity}
                      />

                      {/* Gap duration breakdown if any */}
                      {result.gaps.length > 0 && (
                        <div className="mt-4">
                          <p className="text-xs font-mono mb-2" style={{ color: 'rgba(255,255,255,0.4)' }}>DURATION EXTREMES</p>
                          <div className="grid grid-cols-2 gap-2">
                            {[
                              ['LONGEST', Math.max(...result.gaps.map(g => g.duration_seconds))],
                              ['SHORTEST', Math.min(...result.gaps.map(g => g.duration_seconds))],
                            ].map(([label, seconds]) => (
                              <div key={label} className="glass-card-inset px-3 py-2 text-center">
                                <p className="text-xs font-mono" style={{ color: 'rgba(255,255,255,0.35)' }}>{label}</p>
                                <p className="text-sm font-display font-bold mt-1" style={{ color: '#74c0fc', fontFamily: "'Syne'" }}>
                                  {formatDuration(seconds as number)}
                                </p>
                              </div>
                            ))}
                          </div>
                        </div>
                      )}
                    </div>
                  )}
                </div>

                {/* Right: forensic score + result meta */}
                <div className="flex flex-col gap-4">
                  <div className="glass-card p-5 flex flex-col items-center gap-4">
                    <h3 className="text-sm font-display font-semibold self-start" style={{ fontFamily: "'Syne', sans-serif", color: 'rgba(255,255,255,0.7)' }}>
                      Forensic Score
                    </h3>
                    <ForensicScoreRing
                      score={result.forensic_score.score}
                      riskLevel={result.forensic_score.risk_level}
                    />
                    <p className="text-xs font-mono text-center" style={{ color: 'rgba(255,255,255,0.3)' }}>
                      Composite integrity score<br/>based on gap density, anomalies
                    </p>
                  </div>

                  {result._meta && (
                    <div className="glass-card p-4">
                      <p className="text-xs font-mono mb-2" style={{ color: 'rgba(255,255,255,0.4)' }}>RESULT SAVED</p>
                      <div className="flex items-start gap-2 cursor-pointer" onClick={async () => {
                        const blob = await downloadResult(result._meta!.result_file)
                        const url = URL.createObjectURL(blob)
                        const a = document.createElement('a')
                        a.href = url
                        a.download = result._meta!.result_file
                        a.click()
                        URL.revokeObjectURL(url)
                      }}>
                        <IconFile />
                        <p className="text-xs font-mono break-all" style={{ color: 'rgba(116,192,252,0.7)', textDecoration: 'underline' }}>
                          {result._meta.result_file}
                        </p>
                      </div>
                      <p className="text-xs font-mono mt-2" style={{ color: 'rgba(255,255,255,0.25)' }}>
                        Stored in backend/results/
                      </p>
                    </div>
                  )}

                  {/* Gap density indicator */}
                  <div className="glass-card p-4">
                    <p className="text-xs font-mono mb-3" style={{ color: 'rgba(255,255,255,0.4)' }}>GAP DENSITY</p>
                    <p className="text-2xl font-display font-bold" style={{ fontFamily: "'Syne'", color: result.summary.gap_density_per_1000_lines > 5 ? '#ff4d6d' : '#74c0fc' }}>
                      {result.summary.gap_density_per_1000_lines}
                    </p>
                    <p className="text-xs font-mono" style={{ color: 'rgba(255,255,255,0.3)' }}>per 1,000 lines</p>
                    <div className="mt-2 h-1.5 rounded-full" style={{ background: 'rgba(255,255,255,0.07)' }}>
                      <div
                        className="h-1.5 rounded-full"
                        style={{
                          width: `${Math.min(100, result.summary.gap_density_per_1000_lines * 10)}%`,
                          background: result.summary.gap_density_per_1000_lines > 5 ? '#ff4d6d' : '#74c0fc',
                          transition: 'width 1s ease',
                        }}
                      />
                    </div>
                  </div>
                </div>
              </div>
            </div>
          )}
        </div>
      </div>

      {/* Footer */}
      <footer className="mt-12 text-center">
        <p className="text-xs font-mono" style={{ color: 'rgba(255,255,255,0.2)' }}>
          Log Integrity Monitor · Flask API on :5000 · Next.js on :3000
        </p>
      </footer>
    </main>
  )
}
