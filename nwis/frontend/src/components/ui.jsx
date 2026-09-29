import { useCallback, useEffect, useRef, useState } from 'react'
import { AlertTriangle, Loader2 } from 'lucide-react'

/**
 * Data fetching with loading and error state.
 *
 * `deps` controls refetching. The ref guard stops a slow response from an
 * earlier render overwriting a newer one when the user changes well quickly.
 */
export function useApi(fn, deps = [], { skip = false } = {}) {
  const [data, setData] = useState(null)
  const [error, setError] = useState(null)
  const [loading, setLoading] = useState(!skip)
  const generation = useRef(0)

  const run = useCallback(() => {
    if (skip) {
      setLoading(false)
      return
    }
    const current = ++generation.current
    setLoading(true)
    setError(null)
    fn()
      .then((result) => {
        if (generation.current === current) setData(result)
      })
      .catch((err) => {
        if (generation.current === current) setError(err.message || String(err))
      })
      .finally(() => {
        if (generation.current === current) setLoading(false)
      })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [skip, ...deps])

  useEffect(() => {
    run()
  }, [run])

  return { data, error, loading, reload: run }
}

export function Card({ title, subtitle, actions, className = '', bodyClass = 'p-4', children }) {
  return (
    <section className={`card ${className}`}>
      {(title || actions) && (
        <header className="card-head">
          <div className="min-w-0">
            {title && <h2 className="card-title truncate">{title}</h2>}
            {subtitle && <p className="mt-0.5 text-xs text-slate-500">{subtitle}</p>}
          </div>
          {actions && <div className="flex shrink-0 items-center gap-2">{actions}</div>}
        </header>
      )}
      <div className={bodyClass}>{children}</div>
    </section>
  )
}

export function Stat({ label, value, unit, hint, tone = 'default' }) {
  const tones = {
    default: 'text-slate-900',
    danger: 'text-rose-600',
    warn: 'text-amber-600',
    good: 'text-emerald-600',
  }
  return (
    <div className="card px-4 py-3">
      <p className="stat-label">{label}</p>
      <p className={`stat-value ${tones[tone]}`}>
        {value}
        {unit && <span className="ml-1 text-sm font-medium text-slate-500">{unit}</span>}
      </p>
      {hint && <p className="mt-0.5 text-xs text-slate-500">{hint}</p>}
    </div>
  )
}

export function Chip({ className = '', children }) {
  return <span className={`chip ${className}`}>{children}</span>
}

/** A labelled 0..1 bar, used for relevance dimensions and risk scores. */
export function ScoreBar({ value, color = 'bg-oil-600', height = 'h-1.5', track = 'bg-slate-200' }) {
  const pct = Math.max(0, Math.min(1, value ?? 0)) * 100
  return (
    <div className={`w-full overflow-hidden rounded-full ${track} ${height}`}>
      <div className={`${height} rounded-full ${color} transition-all duration-500`} style={{ width: `${pct}%` }} />
    </div>
  )
}

export function Loading({ label = 'Loading' }) {
  return (
    <div className="flex items-center justify-center gap-2 py-10 text-sm text-slate-500">
      <Loader2 className="h-4 w-4 animate-spin" />
      {label}...
    </div>
  )
}

export function ErrorBox({ message, onRetry }) {
  return (
    <div className="flex items-start gap-3 rounded-lg border border-rose-200 bg-rose-50 p-4 text-sm text-rose-800">
      <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
      <div className="min-w-0 flex-1">
        <p className="font-semibold">Could not load this</p>
        <p className="mt-0.5 break-words text-rose-700">{message}</p>
        {onRetry && (
          <button type="button" className="btn mt-3" onClick={onRetry}>
            Try again
          </button>
        )}
      </div>
    </div>
  )
}

export function Empty({ children }) {
  return <p className="py-8 text-center text-sm text-slate-500">{children}</p>
}

/** Renders loading / error / empty states so views do not each repeat them. */
export function Async({ query, empty, children }) {
  if (query.loading && !query.data) return <Loading />
  if (query.error) return <ErrorBox message={query.error} onRetry={query.reload} />
  if (!query.data) return <Empty>{empty ?? 'No data'}</Empty>
  return children(query.data)
}
