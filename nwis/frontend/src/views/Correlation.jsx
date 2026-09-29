import { useEffect, useState } from 'react'
import { ArrowLeftRight, Link2 } from 'lucide-react'
import { api } from '../api'
import { eventColor, fmt, formationColor } from '../format'
import { Async, Card, Chip, Empty, useApi } from '../components/ui'

/**
 * Two-well correlation panel.
 *
 * The lines joining the two formation columns are the tie points the depth
 * mapping is built from. Once you can see that the Barail sits 200 m deeper
 * in the offset well, "the offset lost returns at 2,840 m" stops being a
 * number and starts being a prediction about where *this* well will meet the
 * same rock.
 */

const PANEL_H = 560
const PAD = 26
const COL_W = 46

// Horizontal layout. The gap between the axis and the left column has to be
// wide enough for the longest formation name, or the labels collide with the
// depth ticks.
const AXIS_X = 74
const LEFT_X = 236
const RIGHT_X = 536
const PANEL_W = 790

/** Round depth ticks, so the axis reads 500 / 1,000 rather than 743 / 1,114. */
function depthTicks(maxDepth) {
  const step = [50, 100, 200, 250, 500, 1000, 2000].find((s) => s >= maxDepth / 8) ?? 2000
  const ticks = []
  for (let d = 0; d <= maxDepth; d += step) ticks.push(d)
  return ticks
}

function Column({ x, tops, tdTvd, events, maxDepth, y, onEvent, side }) {
  return (
    <g>
      {tops.map((t, i) => {
        const next = tops[i + 1]?.top_tvd_m ?? tdTvd ?? maxDepth
        const y0 = y(t.top_tvd_m)
        const h = Math.max(1, y(next) - y0)
        return (
          <g key={t.formation}>
            <rect
              x={x}
              y={y0}
              width={COL_W}
              height={h}
              fill={formationColor(t.formation)}
              stroke="#fff"
              strokeWidth="0.6"
            >
              <title>{`${t.formation} — ${fmt.depth(t.top_tvd_m)} TVD`}</title>
            </rect>
            {h > 14 && (
              <text
                x={side === 'left' ? x - 6 : x + COL_W + 6}
                y={y0 + Math.min(h / 2 + 3, 12)}
                textAnchor={side === 'left' ? 'end' : 'start'}
                className="fill-slate-600 text-[9px]"
              >
                {t.formation}
              </text>
            )}
          </g>
        )
      })}
      {tdTvd != null && (
        <line
          x1={x - 3}
          x2={x + COL_W + 3}
          y1={y(tdTvd)}
          y2={y(tdTvd)}
          stroke="#475569"
          strokeWidth="1.5"
          strokeDasharray="3 2"
        />
      )}
      {events.map((e) => {
        const depth = e.tvd_m
        if (depth == null) return null
        return (
          <circle
            key={e.event_id}
            cx={side === 'left' ? x + COL_W + 10 : x - 10}
            cy={y(depth)}
            r={3 + (e.severity ?? 2) * 0.6}
            fill={eventColor(e.event_type)}
            opacity="0.9"
            className={onEvent ? 'cursor-pointer' : ''}
            onClick={() => onEvent?.(e)}
          >
            <title>{`${e.event_type.replace(/_/g, ' ')} — severity ${e.severity}/5 at ${fmt.depth(
              depth,
            )} TVD`}</title>
          </circle>
        )
      })}
    </g>
  )
}

export default function Correlation({ wellId, offsetId, settings, onPickOffset, onOpenEvidence }) {
  const [chosen, setChosen] = useState(offsetId)

  useEffect(() => {
    if (offsetId) setChosen(offsetId)
  }, [offsetId])

  const offsets = useApi(
    () => api.offsets(wellId, { radius_km: settings.radius, limit: 20, include_correlation: false }),
    [wellId, settings.radius],
  )

  useEffect(() => {
    if (!chosen && offsets.data?.offsets?.length) setChosen(offsets.data.offsets[0].well_id)
  }, [chosen, offsets.data])

  const panel = useApi(() => api.correlation(wellId, chosen), [wellId, chosen], { skip: !chosen })

  return (
    <div className="space-y-4">
      <Card
        title="Cross-well correlation"
        subtitle="Formation tops tie the two wells to a common depth frame"
        actions={
          <select
            className="rounded-lg border border-slate-300 bg-white px-2 py-1.5 text-sm"
            value={chosen ?? ''}
            onChange={(e) => {
              setChosen(e.target.value)
              onPickOffset?.(e.target.value)
            }}
          >
            <option value="" disabled>
              Choose an offset well
            </option>
            {(offsets.data?.offsets ?? []).map((o) => (
              <option key={o.well_id} value={o.well_id}>
                {o.well_name} — relevance {fmt.score(o.relevance_score)} ({o.relevance_band})
              </option>
            ))}
          </select>
        }
        bodyClass="p-4"
      >
        {!chosen ? (
          <Empty>Pick an offset well to correlate against.</Empty>
        ) : (
          <Async query={panel}>
            {(d) => {
              const maxDepth = Math.max(
                d.reference.well.td_tvd_m ?? 0,
                d.offset.well.td_tvd_m ?? 0,
                ...d.reference.formation_tops.map((t) => t.top_tvd_m),
                ...d.offset.formation_tops.map((t) => t.top_tvd_m),
                100,
              )
              const plotH = PANEL_H - PAD * 2
              const y = (depth) => PAD + (Math.min(Math.max(depth, 0), maxDepth) / maxDepth) * plotH
              const leftX = LEFT_X
              const rightX = RIGHT_X
              const corr = d.correlation

              return (
                <div className="space-y-4">
                  <div className="flex flex-wrap items-center gap-3">
                    <Chip className="bg-oil-100 text-oil-800 ring-oil-300">
                      <Link2 className="h-3 w-3" /> {corr.tie_count} formation ties
                    </Chip>
                    <Chip className="bg-slate-100 text-slate-700 ring-slate-300">
                      Mean shift {corr.mean_shift_m > 0 ? '+' : ''}
                      {corr.mean_shift_m.toFixed(0)} m
                    </Chip>
                    <Chip className="bg-slate-100 text-slate-700 ring-slate-300">
                      Spread ±{corr.shift_spread_m.toFixed(0)} m
                    </Chip>
                    <Chip
                      className={
                        corr.quality >= 0.7
                          ? 'bg-emerald-100 text-emerald-800 ring-emerald-300'
                          : corr.quality >= 0.4
                            ? 'bg-amber-100 text-amber-900 ring-amber-300'
                            : 'bg-rose-100 text-rose-800 ring-rose-300'
                      }
                    >
                      Tie quality {corr.quality.toFixed(2)}
                    </Chip>
                    <span className="text-xs text-slate-500">
                      {corr.mean_shift_m > 0
                        ? `${d.offset.well.well_name} sits ${Math.abs(corr.mean_shift_m).toFixed(0)} m deeper on average`
                        : `${d.offset.well.well_name} sits ${Math.abs(corr.mean_shift_m).toFixed(0)} m shallower on average`}
                    </span>
                  </div>

                  <div className="overflow-x-auto">
                    <svg width={PANEL_W} height={PANEL_H} role="img" aria-label="Correlation panel">
                      <text x={leftX} y={14} className="fill-slate-700 text-[11px] font-semibold">
                        {d.reference.well.well_name} (current)
                      </text>
                      <text x={rightX} y={14} className="fill-slate-700 text-[11px] font-semibold">
                        {d.offset.well.well_name}
                      </text>

                      {/* depth axis */}
                      {depthTicks(maxDepth).map((depth) => (
                        <g key={depth}>
                          <line
                            x1={AXIS_X}
                            x2={AXIS_X + 6}
                            y1={y(depth)}
                            y2={y(depth)}
                            stroke="#cbd5e1"
                          />
                          <text
                            x={AXIS_X - 4}
                            y={y(depth) + 3}
                            textAnchor="end"
                            className="fill-slate-400 text-[9px] tabular-nums"
                          >
                            {depth.toLocaleString('en-IN')}
                          </text>
                        </g>
                      ))}
                      <text
                        x={20}
                        y={PANEL_H / 2}
                        textAnchor="middle"
                        className="fill-slate-400 text-[9px]"
                        transform={`rotate(-90 20 ${PANEL_H / 2})`}
                      >
                        TVD (m) — current well scale
                      </text>

                      {/* tie lines */}
                      {corr.ties.map((t) => (
                        <g key={t.formation}>
                          <line
                            x1={leftX + COL_W}
                            x2={rightX}
                            y1={y(t.reference_tvd_m)}
                            y2={y(t.target_tvd_m)}
                            stroke={formationColor(t.formation)}
                            strokeWidth="1.6"
                            opacity="0.75"
                          />
                          <text
                            x={(leftX + COL_W + rightX) / 2}
                            y={(y(t.reference_tvd_m) + y(t.target_tvd_m)) / 2 - 3}
                            textAnchor="middle"
                            className="fill-slate-500 text-[8px]"
                          >
                            {t.shift_m > 0 ? '+' : ''}
                            {t.shift_m.toFixed(0)} m
                          </text>
                        </g>
                      ))}

                      <Column
                        x={leftX}
                        side="left"
                        tops={d.reference.formation_tops}
                        tdTvd={d.reference.well.td_tvd_m}
                        events={[]}
                        maxDepth={maxDepth}
                        y={y}
                      />
                      <Column
                        x={rightX}
                        side="right"
                        tops={d.offset.formation_tops}
                        tdTvd={d.offset.well.td_tvd_m}
                        events={d.projected_events}
                        maxDepth={maxDepth}
                        y={y}
                      />

                      {/* offset events projected onto the current well's scale */}
                      {d.projected_events.map((e) => (
                        <g key={`proj-${e.event_id}`}>
                          <line
                            x1={leftX + COL_W + 4}
                            x2={leftX + COL_W + 16}
                            y1={y(e.projected_tvd_m)}
                            y2={y(e.projected_tvd_m)}
                            stroke={eventColor(e.event_type)}
                            strokeWidth="2"
                          />
                          <circle
                            cx={leftX + COL_W + 22}
                            cy={y(e.projected_tvd_m)}
                            r={3 + (e.severity ?? 2) * 0.5}
                            fill={eventColor(e.event_type)}
                            opacity="0.55"
                          >
                            <title>
                              {`Projected: ${e.event_type.replace(/_/g, ' ')} at ${fmt.depth(
                                e.projected_md_m,
                              )} MD in ${d.reference.well.well_name}`}
                            </title>
                          </circle>
                        </g>
                      ))}
                    </svg>
                  </div>

                  <p className="text-xs text-slate-500">
                    Faded markers on the left are {d.offset.well.well_name}&rsquo;s events projected
                    onto {d.reference.well.well_name}&rsquo;s depth scale through the formation ties
                    — that projection is what the risk engine warns on.
                  </p>
                </div>
              )
            }}
          </Async>
        )}
      </Card>

      {chosen && (
        <Card
          title="Projected events"
          subtitle={`What ${panel.data?.offset?.well?.well_name ?? 'the offset well'} hit, expressed in current-well depths`}
          bodyClass="p-0"
        >
          <Async query={panel}>
            {(d) =>
              d.projected_events.length === 0 ? (
                <Empty>No events recorded in this offset well.</Empty>
              ) : (
                <div className="overflow-x-auto">
                  <table className="w-full text-sm">
                    <thead>
                      <tr className="table-head">
                        <th className="px-4 py-2 text-left">Event</th>
                        <th className="px-4 py-2 text-left">Formation</th>
                        <th className="px-4 py-2 text-right">In offset</th>
                        <th className="px-4 py-2 text-right">
                          <span className="inline-flex items-center gap-1">
                            <ArrowLeftRight className="h-3 w-3" /> Projected here
                          </span>
                        </th>
                        <th className="px-4 py-2 text-center">Sev</th>
                        <th className="px-4 py-2 text-right">NPT</th>
                        <th className="px-4 py-2 text-left">Source</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-slate-100">
                      {d.projected_events.map((e) => (
                        <tr key={e.event_id} className="hover:bg-slate-50">
                          <td className="px-4 py-2">
                            <span className="flex items-center gap-2">
                              <span
                                className="h-2.5 w-2.5 rounded-full"
                                style={{ background: eventColor(e.event_type) }}
                              />
                              {e.event_type.replace(/_/g, ' ').toLowerCase()}
                            </span>
                          </td>
                          <td className="px-4 py-2 text-slate-600">{e.formation ?? '—'}</td>
                          <td className="px-4 py-2 text-right font-mono text-xs">
                            {fmt.depth(e.md_m)}
                          </td>
                          <td className="px-4 py-2 text-right font-mono text-xs font-semibold text-oil-700">
                            {fmt.depth(e.projected_md_m)}
                          </td>
                          <td className="px-4 py-2 text-center">{e.severity}</td>
                          <td className="px-4 py-2 text-right text-xs text-slate-600">
                            {e.npt_hours ? fmt.hours(e.npt_hours) : '—'}
                          </td>
                          <td className="px-4 py-2">
                            {e.citations?.[0] && (
                              <button
                                type="button"
                                className="font-mono text-xs text-oil-700 underline-offset-2 hover:underline"
                                onClick={() => onOpenEvidence?.(e.citations[0])}
                              >
                                {e.citations[0].document_id} p{e.citations[0].page}
                              </button>
                            )}
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
      )}
    </div>
  )
}
