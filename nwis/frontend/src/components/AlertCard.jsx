import { useState } from 'react'
import {
  AlertTriangle, ChevronDown, ChevronRight, FileText, Activity, Lightbulb, MapPin,
  BrainCircuit,
} from 'lucide-react'
import { bandStyle, eventColor, fmt } from '../format'
import { Chip, ScoreBar } from './ui'

/**
 * One look-ahead risk alert.
 *
 * Reads top to bottom the way a driller would ask: what, where, how sure,
 * why, who says so, and what do I do about it.
 */
export default function AlertCard({ alert, onOpenEvidence, defaultOpen = false }) {
  const [open, setOpen] = useState(defaultOpen)
  const style = bandStyle(alert.risk_band)
  const aheadLabel =
    alert.metres_ahead < 10
      ? 'at the bit now'
      : `${Math.round(alert.metres_ahead).toLocaleString('en-IN')} m ahead of the bit`

  return (
    <article className={`card border-l-4 ${style.border}`}>
      <div className="p-4">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="min-w-0 flex-1">
            <div className="flex flex-wrap items-center gap-2">
              <AlertTriangle className="h-4 w-4 shrink-0" style={{ color: style.hex }} />
              <h3 className="text-base font-semibold text-slate-900">{alert.label}</h3>
              <Chip className={style.chip}>{alert.risk_band} risk</Chip>
              {alert.formation && (
                <Chip className="bg-slate-100 text-slate-700 ring-slate-300">{alert.formation}</Chip>
              )}
              {alert.live_boost > 0 && (
                <Chip className="bg-violet-100 text-violet-800 ring-violet-300">
                  <Activity className="h-3 w-3" /> live signal
                </Chip>
              )}
              {alert.model && (
                <Chip className="bg-indigo-100 text-indigo-800 ring-indigo-300">
                  <BrainCircuit className="h-3 w-3" />
                  model {fmt.pct(alert.model.probability)}
                  {alert.model.lift ? ` · ${alert.model.lift}x base` : ''}
                </Chip>
              )}
            </div>
            <p className="mt-1.5 text-sm text-slate-600">
              Expected{' '}
              <b className="text-slate-900">
                {fmt.depth(alert.predicted_md_from)} &ndash; {fmt.depth(alert.predicted_md_to)} MD
              </b>{' '}
              &middot; {aheadLabel}
            </p>
          </div>

          <div className="w-40 shrink-0">
            <div className="flex items-baseline justify-between text-xs text-slate-500">
              <span>Risk</span>
              <span className="font-mono font-semibold text-slate-800">
                {fmt.score(alert.risk_score)}
              </span>
            </div>
            <ScoreBar value={alert.risk_score} color={style.bar} />
            <div className="mt-1.5 flex items-baseline justify-between text-xs text-slate-500">
              <span>Confidence</span>
              <span className="font-mono font-semibold text-slate-800">
                {fmt.score(alert.confidence)}
              </span>
            </div>
            <ScoreBar value={alert.confidence} color="bg-slate-400" />
          </div>
        </div>

        <ul className="mt-3 space-y-1.5">
          {alert.why.map((reason) => (
            <li key={reason} className="flex gap-2 text-sm text-slate-700">
              <span className="mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full" style={{ background: style.hex }} />
              <span>{reason}</span>
            </li>
          ))}
        </ul>

        <div className="mt-3 flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-slate-500">
          {alert.well_count > 0 && (
            <span className="inline-flex items-center gap-1">
              <MapPin className="h-3 w-3" /> {alert.well_count} offset well
              {alert.well_count === 1 ? '' : 's'}
            </span>
          )}
          {alert.evidence_count > 0 && (
            <span className="inline-flex items-center gap-1">
              <FileText className="h-3 w-3" /> {alert.evidence_count} source reference
              {alert.evidence_count === 1 ? '' : 's'}
            </span>
          )}
          {alert.total_npt_hours > 0 && (
            <span>{fmt.hours(alert.total_npt_hours)} NPT in those wells</span>
          )}
        </div>

        <button
          type="button"
          className="btn mt-3"
          onClick={() => setOpen((v) => !v)}
          aria-expanded={open}
        >
          {open ? <ChevronDown className="h-4 w-4" /> : <ChevronRight className="h-4 w-4" />}
          {open ? 'Hide' : 'View'} supporting evidence &amp; recommended actions
        </button>
      </div>

      {open && (
        <div className="space-y-4 border-t border-slate-200 bg-slate-50 p-4">
          {alert.contributing.length === 0 ? (
            <div className="rounded-lg border border-slate-200 bg-white p-3 text-xs leading-relaxed text-slate-600">
              <span className="font-semibold text-slate-700">No event evidence, by design.</span>{' '}
              An overpressured interval is dangerous even where nothing went wrong, because the
              previous crew carried enough mud weight to keep it quiet. This alert is derived from
              the mud weights the offset wells actually carried at the correlated depth, not from
              their event record.
            </div>
          ) : (
          <div>
            <h4 className="text-xs font-semibold uppercase tracking-wide text-slate-500">
              Supporting evidence
            </h4>
            <div className="mt-2 space-y-2">
              {alert.contributing.map((c) => (
                <div key={`${c.well_id}-${c.event_id}`} className="rounded-lg border border-slate-200 bg-white p-3">
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <div className="flex items-center gap-2">
                      <span
                        className="h-2.5 w-2.5 rounded-full"
                        style={{ background: eventColor(alert.event_type) }}
                      />
                      <span className="text-sm font-semibold text-slate-900">{c.well_name}</span>
                      <span className="text-xs text-slate-500">
                        {fmt.km(c.distance_km)} &middot; relevance {fmt.score(c.relevance_score)}
                      </span>
                    </div>
                    <span className="text-xs font-medium text-slate-600">
                      severity {c.severity}/5
                      {c.npt_hours ? ` · ${fmt.hours(c.npt_hours)} NPT` : ''}
                    </span>
                  </div>

                  <p className="mt-1.5 text-xs text-slate-600">
                    Hit it at <b>{fmt.depth(c.offset_md_m)} MD</b> in that well, which correlates to{' '}
                    <b>{fmt.depth(c.projected_md_m)} MD</b> here
                    {c.event_date ? ` · ${fmt.date(c.event_date)}` : ''}
                  </p>

                  {c.citations?.length > 0 && (
                    <div className="mt-2 space-y-1">
                      {c.citations.slice(0, 3).map((cite) => (
                        <button
                          key={`${cite.document_id}-${cite.line_no}`}
                          type="button"
                          onClick={() => onOpenEvidence?.(cite)}
                          className="block w-full rounded border border-slate-200 bg-slate-50 px-2 py-1.5 text-left
                                     text-xs text-slate-700 transition hover:border-oil-300 hover:bg-oil-50"
                        >
                          <span className="font-mono text-[10px] font-semibold text-oil-700">
                            {cite.document_id} &middot; p{cite.page}
                          </span>
                          <span className="mt-0.5 block truncate italic">&ldquo;{cite.snippet}&rdquo;</span>
                        </button>
                      ))}
                      {c.citations.length > 3 && (
                        <p className="pl-1 text-[11px] text-slate-400">
                          +{c.citations.length - 3} more reference
                          {c.citations.length - 3 === 1 ? '' : 's'}
                        </p>
                      )}
                    </div>
                  )}
                </div>
              ))}
            </div>
          </div>
          )}

          {alert.model && (
            <div>
              <h4 className="flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wide text-slate-500">
                <BrainCircuit className="h-3.5 w-3.5" /> Model prediction
              </h4>
              <div className="mt-2 rounded-lg border border-indigo-200 bg-indigo-50 p-3">
                <div className="flex flex-wrap items-baseline gap-x-4 gap-y-1 text-sm">
                  <span className="text-indigo-900">
                    Probability <b>{fmt.pct(alert.model.probability)}</b>
                  </span>
                  {alert.model.base_rate != null && (
                    <span className="text-indigo-700">
                      against a base rate of {fmt.pct(alert.model.base_rate)}
                    </span>
                  )}
                  {alert.model.auc != null && (
                    <span className="ml-auto text-xs text-indigo-600">
                      validated AUC {alert.model.auc.toFixed(2)} (leave-one-well-out)
                    </span>
                  )}
                </div>
                <div className="mt-2">
                  <ScoreBar value={alert.model.probability} color="bg-indigo-500" />
                </div>
                {alert.model.drivers?.length > 0 && (
                  <ul className="mt-2 space-y-0.5">
                    {alert.model.drivers.map((d) => (
                      <li key={d.feature} className="flex justify-between gap-3 text-[11px] text-indigo-800">
                        <span>{d.feature.replace(/_/g, ' ')}</span>
                        <span className="font-mono">
                          {d.value} &rarr; {d.contribution > 0 ? '+' : ''}
                          {d.contribution.toFixed(2)} log-odds
                        </span>
                      </li>
                    ))}
                  </ul>
                )}
                <p className="mt-2 text-[11px] leading-snug text-indigo-700">
                  This is a second opinion, computed from offset incidence, pressure margin and
                  depletion &mdash; not from the evidence count above. Where the two disagree,
                  that disagreement is itself worth reading.
                </p>
              </div>
            </div>
          )}

          <div>
            <h4 className="flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wide text-slate-500">
              <Lightbulb className="h-3.5 w-3.5" /> Recommended actions
            </h4>
            <ul className="mt-2 space-y-1.5">
              {alert.recommended_actions.map((action, i) => (
                <li
                  key={action}
                  className={`flex gap-2 rounded px-2 py-1 text-sm ${
                    action.includes(':') && i < 3
                      ? 'bg-emerald-50 text-emerald-900'
                      : 'text-slate-700'
                  }`}
                >
                  <span className="mt-1 h-1.5 w-1.5 shrink-0 rounded-full bg-emerald-500" />
                  <span>{action}</span>
                </li>
              ))}
            </ul>
            {alert.contributing.length > 0 && (
              <p className="mt-2 text-[11px] text-slate-500">
                Highlighted actions are what the offset crews actually did, taken from their
                reports.
              </p>
            )}
          </div>

          {alert.live_signals?.length > 0 && (
            <div>
              <h4 className="flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wide text-slate-500">
                <Activity className="h-3.5 w-3.5" /> Current-well signals raising this alert
              </h4>
              <ul className="mt-2 space-y-1">
                {alert.live_signals.map((s) => (
                  <li key={s.key} className="rounded bg-violet-50 px-2 py-1.5 text-sm text-violet-900">
                    <b>{s.label}</b> &mdash; {s.note}
                  </li>
                ))}
              </ul>
            </div>
          )}
        </div>
      )}
    </article>
  )
}
