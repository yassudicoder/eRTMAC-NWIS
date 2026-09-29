import { useState } from 'react'
import { FileSearch, FileText, Search } from 'lucide-react'
import { api } from '../api'
import { eventColor, fmt } from '../format'
import { Async, Card, Chip, Empty, useApi } from '../components/ui'

/**
 * The searchable knowledge base.
 *
 * This is the part that answers the "fragmented historical knowledge" problem
 * directly: one place where every report is held, every extracted event is
 * listed, and each one opens the page it came from.
 */
export default function Reports({ wellId, onOpenEvidence }) {
  const [query, setQuery] = useState('')
  const [typeFilter, setTypeFilter] = useState('')
  const [scope, setScope] = useState('well')

  const documents = useApi(
    () => api.documents(scope === 'well' ? { well_id: wellId } : {}),
    [wellId, scope],
  )
  // The event table is always this well's own. Asset-wide event search lives
  // on the Search & Lessons view, which has the query syntax for it; the
  // scope control here applies to the document list above.
  const events = useApi(() => api.wellEvents(wellId), [wellId])
  const stats = useApi(() => api.stats(), [])

  const docs = (documents.data ?? []).filter((d) => {
    if (typeFilter && d.doc_type !== typeFilter) return false
    if (!query) return true
    const q = query.toLowerCase()
    return d.title.toLowerCase().includes(q) || d.document_id.toLowerCase().includes(q)
  })

  return (
    <div className="grid gap-4 xl:grid-cols-3">
      <div className="space-y-4 xl:col-span-2">
        <Card
          title="Source documents"
          subtitle={`${docs.length} report${docs.length === 1 ? '' : 's'} held`}
          actions={
            <div className="flex items-center gap-2">
              <div className="relative">
                <Search className="pointer-events-none absolute left-2 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-slate-400" />
                <input
                  type="search"
                  value={query}
                  onChange={(e) => setQuery(e.target.value)}
                  placeholder="Search reports"
                  className="w-44 rounded-lg border border-slate-300 py-1.5 pl-7 pr-2 text-sm"
                />
              </div>
              <select
                className="rounded-lg border border-slate-300 bg-white px-2 py-1.5 text-sm"
                value={typeFilter}
                onChange={(e) => setTypeFilter(e.target.value)}
              >
                <option value="">All types</option>
                <option value="WCR">Completion report</option>
                <option value="DDR">Daily reports</option>
                <option value="MUDLOG">Mud log</option>
              </select>
              <select
                className="rounded-lg border border-slate-300 bg-white px-2 py-1.5 text-sm"
                value={scope}
                onChange={(e) => setScope(e.target.value)}
                title="Which wells' reports to list"
              >
                <option value="well">This well</option>
                <option value="all">All wells</option>
              </select>
            </div>
          }
          bodyClass="p-0"
        >
          <Async query={documents} empty="No documents">
            {() =>
              docs.length === 0 ? (
                <Empty>No reports match that search.</Empty>
              ) : (
                <ul className="max-h-[420px] divide-y divide-slate-100 overflow-auto">
                  {docs.map((d) => (
                    <li key={d.document_id}>
                      <button
                        type="button"
                        className="flex w-full items-center gap-3 px-4 py-3 text-left transition hover:bg-slate-50"
                        onClick={() =>
                          onOpenEvidence({
                            document_id: d.document_id,
                            doc_type: d.doc_type,
                            page: 1,
                            line_no: 1,
                            snippet: `${d.title} — opened from the report library`,
                          })
                        }
                      >
                        <FileText className="h-4 w-4 shrink-0 text-oil-700" />
                        <div className="min-w-0 flex-1">
                          <p className="truncate text-sm font-medium text-slate-800">{d.title}</p>
                          <p className="text-xs text-slate-500">
                            {d.document_id} &middot; {d.pages} page{d.pages === 1 ? '' : 's'} &middot;{' '}
                            {fmt.number(d.characters)} characters
                          </p>
                        </div>
                        <Chip className="bg-slate-100 text-slate-600 ring-slate-300">{d.doc_type}</Chip>
                      </button>
                    </li>
                  ))}
                </ul>
              )
            }
          </Async>
        </Card>

        <Card
          title="Events extracted from this well's reports"
          subtitle="Every row was read out of report prose, not entered by hand"
          bodyClass="p-0"
        >
          <Async query={events} empty="No events">
            {(rows) =>
              rows.length === 0 ? (
                <Empty>No drilling events were extracted for this well.</Empty>
              ) : (
                <div className="max-h-[460px] overflow-auto">
                  <table className="w-full text-sm">
                    <thead>
                      <tr className="table-head">
                        <th className="px-4 py-2 text-left">Event</th>
                        <th className="px-4 py-2 text-left">Formation</th>
                        <th className="px-4 py-2 text-right">Depth</th>
                        <th className="px-4 py-2 text-center">Sev</th>
                        <th className="px-4 py-2 text-right">NPT</th>
                        <th className="px-4 py-2 text-center">Conf.</th>
                        <th className="px-4 py-2 text-left">Evidence</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-slate-100">
                      {rows.map((e) => (
                        <tr key={e.event_id} className="hover:bg-slate-50">
                          <td className="px-4 py-2">
                            <span className="flex items-center gap-2">
                              <span
                                className="h-2.5 w-2.5 shrink-0 rounded-full"
                                style={{ background: eventColor(e.event_type) }}
                              />
                              <span className="capitalize">
                                {e.event_type.replace(/_/g, ' ').toLowerCase()}
                              </span>
                            </span>
                          </td>
                          <td className="px-4 py-2 text-slate-600">{e.formation ?? '—'}</td>
                          <td className="px-4 py-2 text-right font-mono text-xs">
                            {fmt.depth(e.md_m)}
                          </td>
                          <td className="px-4 py-2 text-center font-semibold">{e.severity}</td>
                          <td className="px-4 py-2 text-right text-xs text-slate-600">
                            {e.npt_hours ? fmt.hours(e.npt_hours) : '—'}
                          </td>
                          <td className="px-4 py-2 text-center">
                            <span
                              className={`font-mono text-xs ${
                                e.needs_review ? 'text-amber-600' : 'text-slate-500'
                              }`}
                            >
                              {fmt.pct(e.confidence)}
                            </span>
                          </td>
                          <td className="px-4 py-2">
                            <div className="flex flex-wrap gap-1">
                              {(e.citations ?? []).slice(0, 3).map((c) => (
                                <button
                                  key={`${c.document_id}-${c.line_no}`}
                                  type="button"
                                  className="rounded bg-oil-50 px-1.5 py-0.5 font-mono text-[10px] text-oil-700 hover:bg-oil-100"
                                  onClick={() => onOpenEvidence(c)}
                                >
                                  {c.doc_type} p{c.page}
                                </button>
                              ))}
                              {(e.citations?.length ?? 0) > 3 && (
                                <span className="text-[10px] text-slate-400">
                                  +{e.citations.length - 3}
                                </span>
                              )}
                            </div>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )
            }
          </Async>
        </Card>
      </div>

      <div className="space-y-4">
        <Card title="Ingestion" subtitle="Last pipeline run" bodyClass="p-4">
          <Async query={stats}>
            {(s) => {
              const run = s.last_ingest
              if (!run) return <Empty>No ingestion run recorded.</Empty>
              return (
                <dl className="space-y-2 text-xs">
                  {[
                    ['Documents read', fmt.number(run.documents)],
                    ['Pages read', fmt.number(run.pages)],
                    ['Lines scanned', fmt.number(run.lines)],
                    ['Raw mentions found', fmt.number(run.raw_events)],
                    ['After evidence merge', fmt.number(run.merged_events)],
                    ['Flagged for review', fmt.number(run.needs_review)],
                    ['Processing time', `${run.seconds}s`],
                  ].map(([k, v]) => (
                    <div key={k} className="flex justify-between gap-3">
                      <dt className="text-slate-500">{k}</dt>
                      <dd className="font-mono font-medium text-slate-800">{v}</dd>
                    </div>
                  ))}
                  <div className="border-t border-slate-100 pt-2">
                    <p className="mb-1 text-slate-500">Mentions by report type</p>
                    {Object.entries(run.by_doc_type ?? {}).map(([k, v]) => (
                      <div key={k} className="flex justify-between gap-3">
                        <dt className="text-slate-600">{k}</dt>
                        <dd className="font-mono text-slate-800">{fmt.number(v)}</dd>
                      </div>
                    ))}
                  </div>
                </dl>
              )
            }}
          </Async>
        </Card>

        <Card title="How extraction works" bodyClass="p-4">
          <ol className="space-y-2.5 text-xs leading-relaxed text-slate-600">
            {[
              ['Intake', 'Text, PDF text layer, or Tesseract OCR for scanned pages. Page and line numbers are kept throughout.'],
              ['Classify', 'A hazard lexicon fires on report phrasing. Blockers stop false hits — "Kick Off Point" is not a kick.'],
              ['Parse', 'Depths, loss rates, pit gains, overpull and NPT are pulled from the same sentence.'],
              ['Normalise', 'Formation aliases are mapped to one canonical column; MD is converted to TVD.'],
              ['Aggregate', 'The same event in the WCR, the DDR and the mud log becomes one record with three citations.'],
            ].map(([step, body], i) => (
              <li key={step} className="flex gap-2">
                <span className="mt-0.5 flex h-4 w-4 shrink-0 items-center justify-center rounded-full bg-oil-700 text-[9px] font-bold text-white">
                  {i + 1}
                </span>
                <span>
                  <b className="text-slate-800">{step}.</b> {body}
                </span>
              </li>
            ))}
          </ol>
          <p className="mt-3 flex gap-1.5 rounded bg-slate-50 p-2 text-[11px] leading-snug text-slate-500">
            <FileSearch className="mt-0.5 h-3 w-3 shrink-0" />
            Extractions below the confidence threshold are flagged rather than dropped, so a person
            can confirm them instead of the system guessing.
          </p>
        </Card>
      </div>
    </div>
  )
}
