import { useState } from 'react'
import { Check, ChevronDown, ChevronRight, GitCompare, X } from 'lucide-react'
import { bandStyle, fmt } from '../format'
import { Chip, ScoreBar } from './ui'

/**
 * The ranked offset-well list, with each well's dimension breakdown.
 *
 * The breakdown matters more than the ranking: it is the difference between
 * "trust me, this well is relevant" and "this well is relevant because it
 * shares six formation tops, covers the interval, and was drilled with the
 * same mud weight".
 */
export default function OffsetList({ offsets, selectedId, onSelect, onCompare }) {
  const [expanded, setExpanded] = useState(() => new Set())

  const toggle = (id) =>
    setExpanded((prev) => {
      const next = new Set(prev)
      next.has(id) ? next.delete(id) : next.add(id)
      return next
    })

  if (!offsets?.length) {
    return (
      <p className="py-8 text-center text-sm text-slate-500">
        No wells found within this radius. Try widening the search.
      </p>
    )
  }

  return (
    <ol className="divide-y divide-slate-200">
      {offsets.map((o, index) => {
        const style = bandStyle(o.relevance_band)
        const isOpen = expanded.has(o.well_id)
        const isSelected = selectedId === o.well_id

        return (
          <li
            key={o.well_id}
            className={`px-4 py-3 transition ${isSelected ? 'bg-oil-50' : 'hover:bg-slate-50'}`}
          >
            <div className="flex items-start gap-3">
              <span className="mt-0.5 w-5 shrink-0 text-right text-xs font-semibold text-slate-400">
                {index + 1}
              </span>

              <button
                type="button"
                className="min-w-0 flex-1 text-left"
                onClick={() => onSelect?.(o.well_id)}
              >
                <div className="flex flex-wrap items-center gap-2">
                  <span className="text-sm font-semibold text-slate-900">{o.well_name}</span>
                  <Chip className={style.chip}>
                    <span className={`h-1.5 w-1.5 rounded-full ${style.dot}`} />
                    {o.relevance_band}
                  </Chip>
                  <span className="font-mono text-xs text-slate-500">{fmt.score(o.relevance_score)}</span>
                </div>
                <p className="mt-0.5 text-xs text-slate-500">
                  {fmt.km(o.distance_km)} {o.bearing} &middot; {o.well_type} &middot; TD{' '}
                  {fmt.depth(o.td_md_m)} &middot; {fmt.year(o.completion_date)} &middot;{' '}
                  {o.event_count} events
                </p>
                <div className="mt-1.5">
                  <ScoreBar value={o.relevance_score} color={style.bar} />
                </div>
              </button>

              <div className="flex shrink-0 items-center gap-1">
                {onCompare && (
                  <button
                    type="button"
                    className="btn !px-2 !py-1"
                    title={`Correlate with ${o.well_name}`}
                    onClick={() => onCompare(o.well_id)}
                  >
                    <GitCompare className="h-3.5 w-3.5" />
                  </button>
                )}
                <button
                  type="button"
                  className="btn !px-2 !py-1"
                  onClick={() => toggle(o.well_id)}
                  aria-expanded={isOpen}
                  aria-label="Toggle scoring breakdown"
                >
                  {isOpen ? <ChevronDown className="h-3.5 w-3.5" /> : <ChevronRight className="h-3.5 w-3.5" />}
                </button>
              </div>
            </div>

            {isOpen && (
              <div className="mt-3 space-y-3 rounded-lg border border-slate-200 bg-slate-50 p-3">
                <div className="space-y-2">
                  {o.dimensions.map((d) => (
                    <div key={d.key}>
                      <div className="flex items-baseline justify-between gap-2 text-xs">
                        <span className="text-slate-700">{d.label}</span>
                        <span className="shrink-0 font-mono text-slate-500">
                          {d.score.toFixed(2)} &times; {d.weight.toFixed(2)} ={' '}
                          <b className="text-slate-700">{d.contribution.toFixed(3)}</b>
                        </span>
                      </div>
                      <div className="mt-1">
                        <ScoreBar value={d.score} color="bg-oil-500" height="h-1" />
                      </div>
                      <p className="mt-0.5 text-[11px] text-slate-500">{d.detail}</p>
                    </div>
                  ))}
                </div>

                {o.supports?.length > 0 && (
                  <ul className="space-y-1">
                    {o.supports.map((s) => (
                      <li key={s} className="flex gap-1.5 text-xs text-emerald-800">
                        <Check className="mt-0.5 h-3 w-3 shrink-0" />
                        {s}
                      </li>
                    ))}
                  </ul>
                )}
                {o.caveats?.length > 0 && (
                  <ul className="space-y-1">
                    {o.caveats.map((c) => (
                      <li key={c} className="flex gap-1.5 text-xs text-rose-700">
                        <X className="mt-0.5 h-3 w-3 shrink-0" />
                        {c}
                      </li>
                    ))}
                  </ul>
                )}

                {o.correlation && (
                  <p className="border-t border-slate-200 pt-2 text-[11px] text-slate-500">
                    Depth correlation: {o.correlation.tie_count} shared formation ties, mean shift{' '}
                    {o.correlation.mean_shift_m > 0 ? '+' : ''}
                    {o.correlation.mean_shift_m.toFixed(0)} m, spread{' '}
                    {o.correlation.shift_spread_m.toFixed(0)} m, quality{' '}
                    {o.correlation.quality.toFixed(2)}
                  </p>
                )}
              </div>
            )}
          </li>
        )
      })}
    </ol>
  )
}
