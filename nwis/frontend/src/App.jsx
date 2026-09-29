import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  Activity, BarChart3, FileText, GitCompare, LayoutDashboard, MapPin, Pause, Play,
  Radio, RotateCcw, Search, TriangleAlert,
} from 'lucide-react'
import { api } from './api'
import { fmt } from './format'
import { ErrorBox, Loading } from './components/ui'
import EvidenceDrawer from './components/EvidenceDrawer'
import Overview from './views/Overview'
import NearbyWells from './views/NearbyWells'
import Correlation from './views/Correlation'
import RiskAlerts from './views/RiskAlerts'
import Knowledge from './views/Knowledge'
import Reports from './views/Reports'
import Analytics from './views/Analytics'

const NAV = [
  { key: 'overview', label: 'Overview', icon: LayoutDashboard },
  { key: 'offsets', label: 'Nearby Wells', icon: MapPin },
  { key: 'correlation', label: 'Correlation', icon: GitCompare },
  { key: 'risk', label: 'Risk Alerts', icon: TriangleAlert },
  { key: 'knowledge', label: 'Search & Lessons', icon: Search },
  { key: 'reports', label: 'Reports', icon: FileText },
  { key: 'analytics', label: 'Analytics', icon: BarChart3 },
]

/**
 * Replays the recorded drilling feed as Server-Sent Events, the way a live
 * eRTMAC connection would deliver it. Alerts are re-issued by the server as
 * the simulated bit advances.
 */
function useReplay(wellId, settings) {
  const [running, setRunning] = useState(false)
  const [frame, setFrame] = useState(null)
  const [progress, setProgress] = useState(null)
  // The look-ahead the server re-ran for the simulated bit depth. This is the
  // whole point of the replay: as the bit advances, the alerts change.
  const [liveRisk, setLiveRisk] = useState(null)
  const source = useRef(null)

  const stop = useCallback(() => {
    source.current?.close()
    source.current = null
    setRunning(false)
  }, [])

  const reset = useCallback(() => {
    stop()
    setFrame(null)
    setProgress(null)
    setLiveRisk(null)
  }, [stop])

  const start = useCallback(() => {
    stop()
    const es = new EventSource(
      api.streamUrl(wellId, {
        interval_ms: 500,
        lookahead_m: settings.lookahead,
        radius_km: settings.radius,
      }),
    )
    es.addEventListener('frame', (e) => {
      const data = JSON.parse(e.data)
      setFrame(data)
      setProgress({ index: data.index, total: data.total })
    })
    // The backend recomputes the whole look-ahead every few frames and pushes
    // it down this stream. Ignoring it would make the replay a moving number
    // with nothing behind it.
    es.addEventListener('alerts', (e) => setLiveRisk(JSON.parse(e.data)))
    es.addEventListener('end', () => stop())
    es.onerror = () => stop()
    source.current = es
    setRunning(true)
  }, [wellId, settings.lookahead, settings.radius, stop])

  useEffect(() => reset, [reset, wellId])

  return { running, frame, progress, liveRisk, start, stop, reset }
}

export default function App() {
  const [view, setView] = useState('overview')
  const [wellId, setWellId] = useState(null)
  const [wells, setWells] = useState([])
  const [bootError, setBootError] = useState(null)
  const [evidence, setEvidence] = useState(null)
  const [compareWith, setCompareWith] = useState(null)
  const [settings, setSettings] = useState({ radius: 15, lookahead: 300 })

  useEffect(() => {
    api
      .wells()
      .then((rows) => {
        setWells(rows)
        const drilling = rows.find((w) => w.status === 'Drilling')
        setWellId(drilling?.well_id ?? rows[0]?.well_id ?? null)
      })
      .catch((e) => setBootError(e.message))
  }, [])

  const replay = useReplay(wellId, settings)
  const currentWell = useMemo(() => wells.find((w) => w.well_id === wellId), [wells, wellId])

  const openCorrelation = useCallback((offsetId) => {
    setCompareWith(offsetId)
    setView('correlation')
  }, [])

  if (bootError) {
    return (
      <div className="flex h-full items-center justify-center p-8">
        <div className="max-w-lg">
          <ErrorBox message={bootError} onRetry={() => window.location.reload()} />
          <p className="mt-4 text-sm text-slate-600">
            The API may not be running. Start it with:
          </p>
          <pre className="mt-2 overflow-x-auto rounded bg-slate-900 p-3 text-xs text-slate-100">
            cd backend && uvicorn app.main:app --reload
          </pre>
        </div>
      </div>
    )
  }

  if (!wellId) return <Loading label="Connecting to NWIS" />

  const drillingWells = wells.filter((w) => w.status === 'Drilling')
  const otherWells = wells.filter((w) => w.status !== 'Drilling')

  return (
    <div className="flex h-full">
      {/* sidebar */}
      <nav className="flex w-60 shrink-0 flex-col bg-oil-950 px-3 py-4">
        <div className="px-2 pb-4">
          <p className="text-xl font-bold tracking-tight text-white">
            NW<span className="text-orange-400">i</span>S
          </p>
          <p className="mt-0.5 text-[11px] leading-tight text-oil-300">
            Nearby Wells Intelligence System
          </p>
          <p className="mt-2 text-[10px] uppercase tracking-wider text-oil-400">
            Oil India Limited &middot; Upper Assam
          </p>
        </div>

        <div className="space-y-1">
          {NAV.map(({ key, label, icon: Icon }) => (
            <button
              key={key}
              type="button"
              onClick={() => setView(key)}
              className={`nav-item ${view === key ? 'nav-item-active' : ''}`}
            >
              <Icon className="h-4 w-4 shrink-0" />
              {label}
            </button>
          ))}
        </div>

        <div className="mt-auto space-y-3 px-2 pt-4">
          <div className="rounded-lg bg-white/5 p-3">
            <p className="flex items-center gap-1.5 text-[10px] font-semibold uppercase tracking-wide text-oil-300">
              <Radio className="h-3 w-3" /> eRTMAC replay
            </p>
            <p className="mt-1 text-[11px] leading-snug text-oil-200">
              Replays this well&rsquo;s recorded feed; alerts refresh as the bit advances.
            </p>
            {replay.frame && (
              <p className="mt-1.5 font-mono text-[11px] text-white">
                {fmt.depth(replay.frame.md_m)} &middot; ROP {replay.frame.rop_m_hr?.toFixed(1)} m/hr
              </p>
            )}
            {replay.progress && (
              <div className="mt-1.5 h-1 w-full overflow-hidden rounded-full bg-white/10">
                <div
                  className="h-1 rounded-full bg-orange-400 transition-all"
                  style={{
                    width: `${((replay.progress.index + 1) / replay.progress.total) * 100}%`,
                  }}
                />
              </div>
            )}
            <div className="mt-2 flex gap-1">
              <button
                type="button"
                className="btn flex-1 justify-center !px-2 !py-1 text-xs"
                onClick={replay.running ? replay.stop : replay.start}
              >
                {replay.running ? <Pause className="h-3 w-3" /> : <Play className="h-3 w-3" />}
                {replay.running ? 'Pause' : 'Play'}
              </button>
              <button
                type="button"
                className="btn !px-2 !py-1 text-xs"
                onClick={replay.reset}
                title="Reset replay"
              >
                <RotateCcw className="h-3 w-3" />
              </button>
            </div>
          </div>
          <p className="px-1 text-[10px] leading-snug text-oil-400">
            Prototype running on a synthetic Upper Assam dataset.
          </p>
        </div>
      </nav>

      {/* main */}
      <div className="flex min-w-0 flex-1 flex-col">
        <header className="flex flex-wrap items-center gap-3 border-b border-slate-200 bg-white px-5 py-3">
          <div className="min-w-0">
            <h1 className="text-lg font-semibold text-slate-900">
              {NAV.find((n) => n.key === view)?.label}
            </h1>
            {currentWell && (
              <p className="text-xs text-slate-500">
                {currentWell.well_name} &middot; {currentWell.field_name} field &middot;{' '}
                {currentWell.status === 'Drilling'
                  ? `bit at ${fmt.depth(currentWell.current_bit_md_m)} MD`
                  : `TD ${fmt.depth(currentWell.td_md_m)} MD`}
              </p>
            )}
          </div>

          <div className="ml-auto flex items-center gap-3">
            {replay.running && (
              <span className="flex items-center gap-1.5 rounded-full bg-emerald-50 px-2.5 py-1 text-xs font-medium text-emerald-700 ring-1 ring-emerald-200">
                <Activity className="h-3 w-3 animate-pulse" />
                Live feed
              </span>
            )}
            <label className="sr-only" htmlFor="well-select">
              Current well
            </label>
            <select
              id="well-select"
              className="w-64 rounded-lg border border-slate-300 bg-white px-3 py-1.5 text-sm"
              value={wellId}
              onChange={(e) => {
                setWellId(e.target.value)
                setCompareWith(null)
              }}
            >
              <optgroup label="Currently drilling">
                {drillingWells.map((w) => (
                  <option key={w.well_id} value={w.well_id}>
                    {w.well_name} — drilling
                  </option>
                ))}
              </optgroup>
              <optgroup label="Completed wells">
                {otherWells.map((w) => (
                  <option key={w.well_id} value={w.well_id}>
                    {w.well_name} — {w.field_name}
                  </option>
                ))}
              </optgroup>
            </select>
          </div>
        </header>

        <main className="flex-1 overflow-auto bg-slate-100 p-5">
          {view === 'overview' && (
            <Overview
              wellId={wellId}
              settings={settings}
              onOpenEvidence={setEvidence}
              onNavigate={setView}
              liveRisk={replay.liveRisk}
              liveFrame={replay.frame}
            />
          )}
          {view === 'offsets' && (
            <NearbyWells
              wellId={wellId}
              settings={settings}
              onSettings={setSettings}
              onCompare={openCorrelation}
            />
          )}
          {view === 'correlation' && (
            <Correlation
              wellId={wellId}
              offsetId={compareWith}
              settings={settings}
              onPickOffset={setCompareWith}
              onOpenEvidence={setEvidence}
            />
          )}
          {view === 'risk' && (
            <RiskAlerts
              wellId={wellId}
              settings={settings}
              onSettings={setSettings}
              onOpenEvidence={setEvidence}
              liveRisk={replay.liveRisk}
              liveFrame={replay.frame}
            />
          )}
          {view === 'knowledge' && (
            <Knowledge
              wellId={wellId}
              onOpenEvidence={setEvidence}
              onOpenWell={(id) => {
                setWellId(id)
                setView('overview')
              }}
            />
          )}
          {view === 'reports' && <Reports wellId={wellId} onOpenEvidence={setEvidence} />}
          {view === 'analytics' && <Analytics />}
        </main>
      </div>

      <EvidenceDrawer citation={evidence} onClose={() => setEvidence(null)} />
    </div>
  )
}
