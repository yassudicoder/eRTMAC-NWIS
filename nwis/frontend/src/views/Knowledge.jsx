import { useEffect, useMemo, useState } from 'react'
import {
  BookOpen, FileText, Lightbulb, Search as SearchIcon, TriangleAlert,
} from 'lucide-react'
import { api } from '../api'
import { eventColor, fmt, formationColor } from '../format'
import { Async, Card, Chip, Empty, ErrorBox, Loading, useApi } from '../components/ui'

/**
 * The searchable knowledge repository.
 *
 * One box over everything the asset knows: the structured events, the lessons
 * previous crews wrote for the next well, and the raw text of every page of
 * every report. A hit opens the thing it found - an event with its evidence,
 * a lesson with its source, or the report page itself.
 */

const KINDS = [
  { key: '', label: 'Everything' },
  { key: 'event', label: 'Events' },
  { key: 'lesson', label: 'Lessons' },
  { key: 'document', label: 'Report text' },
]

const EXAMPLES = [
  'differential sticking',
  'poor cement bond',
  'total loss of returns',
  'coal cavings',
  'overpressured shale',
]

/** Render `<<term>>` markers from the FTS snippet as highlights. */
function Highlighted({ text }) {
  const parts = useMemo(() => (text ?? '').split(/(<<[^>]*>>)/g), [text])
  return (
    <span>
      {parts.map((part, i) =>
        part.startsWith('<<') && part.endsWith('>>') ? (
          <mark key={i} className="rounded bg-amber-200 px-0.5 text-slate-900">
            {part.slice(2, -2)}
          </mark>
        ) : (
          <span key={i}>{part}</span>
        ),
      )}
    </span>
  )
}

const KIND_META = {
  event: { icon: TriangleAlert, label: 'Event', chip: 'bg-rose-100 text-rose-800 ring-rose-300' },
  lesson: { icon: Lightbulb, label: 'Lesson', chip: 'bg-amber-100 text-amber-900 ring-amber-300' },
  document: { icon: FileText, label: 'Report', chip: 'bg-slate-100 text-slate-700 ring-slate-300' },
}

export default function Knowledge({ wellId, onOpenEvidence, onOpenWell }) {
  const [query, setQuery] = useState('')
  const [submitted, setSubmitted] = useState('')
  const [kind, setKind] = useState('')
  const [scopeToWell, setScopeToWell] = useState(false)
  const [results, setResults] = useState(null)
  const [error, setError] = useState(null)
  const [loading, setLoading] = useState(false)

  useEffect(() => {
    if (!submitted) {
      setResults(null)
      return
    }
    let live = true
    setLoading(true)
    setError(null)
    api
      .search(submitted, { kind: kind || undefined, well_id: scopeToWell ? wellId : undefined, limit: 60 })
      .then((d) => live && setResults(d))
      .catch((e) => live && setError(e.message))
      .finally(() => live && setLoading(false))
    return () => {
      live = false
    }
  }, [submitted, kind, scopeToWell, wellId])

  const lessons = useApi(() => api.lessons({ limit: 60 }), [])

  const openHit = (hit) => {
    if (hit.kind === 'document') {
      onOpenEvidence({
        document_id: hit.open.document_id,
        doc_type: 'REPORT',
        page: hit.open.page,
        line_no: hit.open.line_no,
        snippet: (hit.excerpt ?? '').replace(/<<|>>/g, ''),
      })
    } else {
      onOpenWell?.(hit.well_id)
    }
  }

  return (
    <div className="space-y-4">
      <Card
        title="Search the knowledge base"
        subtitle="Events, lessons learnt and the full text of every report"
        bodyClass="p-4 space-y-3"
      >
        <form
          onSubmit={(e) => {
            e.preventDefault()
            setSubmitted(query.trim())
          }}
          className="flex flex-wrap gap-2"
        >
          <div className="relative min-w-0 flex-1">
            <SearchIcon className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-slate-400" />
            <input
              type="search"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="e.g. differential sticking in the Tipam"
              className="w-full rounded-lg border border-slate-300 py-2 pl-9 pr-3 text-sm"
            />
          </div>
          <select
            className="rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm"
            value={kind}
            onChange={(e) => setKind(e.target.value)}
          >
            {KINDS.map((k) => (
              <option key={k.key} value={k.key}>
                {k.label}
              </option>
            ))}
          </select>
          <label className="flex items-center gap-2 rounded-lg border border-slate-300 px-3 py-2 text-sm">
            <input
              type="checkbox"
              checked={scopeToWell}
              onChange={(e) => setScopeToWell(e.target.checked)}
              className="accent-oil-600"
            />
            This well only
          </label>
          <button type="submit" className="btn btn-primary">
            Search
          </button>
        </form>

        <div className="flex flex-wrap items-center gap-2 text-xs text-slate-500">
          <span>Try:</span>
          {EXAMPLES.map((ex) => (
            <button
              key={ex}
              type="button"
              className="rounded-full bg-slate-100 px-2.5 py-1 text-slate-700 transition hover:bg-oil-100"
              onClick={() => {
                setQuery(ex)
                setSubmitted(ex)
              }}
            >
              {ex}
            </button>
          ))}
        </div>
      </Card>

      {loading && <Loading label="Searching" />}
      {error && <ErrorBox message={error} />}

      {results && !loading && (
        <Card
          title={`${results.count} result${results.count === 1 ? '' : 's'} for "${results.query}"`}
          subtitle={Object.entries(results.by_kind)
            .map(([k, n]) => `${n} ${KIND_META[k]?.label.toLowerCase() ?? k}`)
            .join(' · ')}
          bodyClass="p-0"
        >
          {results.results.length === 0 ? (
            <Empty>Nothing matched. Try fewer or more general words.</Empty>
          ) : (
            <ul className="max-h-[560px] divide-y divide-slate-100 overflow-auto">
              {results.results.map((hit) => {
                const meta = KIND_META[hit.kind] ?? KIND_META.document
                const Icon = meta.icon
                return (
                  <li key={`${hit.kind}-${hit.ref_id}`}>
                    <button
                      type="button"
                      onClick={() => openHit(hit)}
                      className="flex w-full items-start gap-3 px-4 py-3 text-left transition hover:bg-slate-50"
                    >
                      <Icon className="mt-0.5 h-4 w-4 shrink-0 text-slate-400" />
                      <div className="min-w-0 flex-1">
                        <div className="flex flex-wrap items-center gap-2">
                          <Chip className={meta.chip}>{meta.label}</Chip>
                          <span className="text-sm font-medium text-slate-800">
                            {hit.well_name}
                          </span>
                          <span className="text-xs text-slate-500">{hit.title}</span>
                        </div>
                        <p className="mt-1 text-sm leading-relaxed text-slate-600">
                          <Highlighted text={hit.excerpt} />
                        </p>
                      </div>
                    </button>
                  </li>
                )
              })}
            </ul>
          )}
        </Card>
      )}

      <Card
        title="Lessons learnt"
        subtitle="Recommendations previous crews recorded for the next well, read out of the completion reports"
        actions={<BookOpen className="h-4 w-4 text-oil-700" />}
        bodyClass="p-0"
      >
        <Async query={lessons} empty="No lessons captured">
          {(d) =>
            d.lessons.length === 0 ? (
              <Empty>No lessons were captured from the reports.</Empty>
            ) : (
              <ul className="max-h-[520px] divide-y divide-slate-100 overflow-auto">
                {d.lessons.map((l) => (
                  <li key={l.lesson_id} className="px-4 py-3">
                    <div className="flex flex-wrap items-center gap-2">
                      {l.formation && (
                        <span className="flex items-center gap-1.5 text-xs text-slate-600">
                          <span
                            className="h-3.5 w-1.5 rounded"
                            style={{ background: formationColor(l.formation) }}
                          />
                          {l.formation}
                        </span>
                      )}
                      {l.hazard_type && (
                        <Chip className="bg-slate-100 text-slate-700 ring-slate-300">
                          <span
                            className="h-1.5 w-1.5 rounded-full"
                            style={{ background: eventColor(l.hazard_type) }}
                          />
                          {l.hazard_label}
                        </Chip>
                      )}
                      <span className="text-xs text-slate-500">
                        {l.well_name} · {l.field_name}
                      </span>
                      {l.md_m != null && (
                        <span className="ml-auto font-mono text-xs text-slate-500">
                          {fmt.depth(l.md_m)} MD
                        </span>
                      )}
                    </div>
                    <p className="mt-1.5 text-sm text-slate-700">{l.text}</p>
                    {l.document_id && (
                      <button
                        type="button"
                        className="mt-1 font-mono text-[11px] text-oil-700 underline-offset-2 hover:underline"
                        onClick={() =>
                          onOpenEvidence({
                            document_id: l.document_id,
                            doc_type: l.doc_type,
                            page: l.page,
                            line_no: l.line_no,
                            snippet: l.text,
                          })
                        }
                      >
                        {l.document_id} · p{l.page}
                      </button>
                    )}
                  </li>
                ))}
              </ul>
            )
          }
        </Async>
      </Card>
    </div>
  )
}
