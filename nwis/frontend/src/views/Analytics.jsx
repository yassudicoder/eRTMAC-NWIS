import { Radar } from 'lucide-react'
import { api } from '../api'
import { eventColor, fmt, formationColor } from '../format'
import { Async, Card, Chip, ScoreBar, Stat, useApi } from '../components/ui'

/** Horizontal bar list — enough for these rankings, and no chart dependency. */
function BarList({ rows, valueKey, labelKey, colorFor, unit }) {
  const max = Math.max(...rows.map((r) => r[valueKey]), 1)
  return (
    <ul className="space-y-2.5">
      {rows.map((r) => (
        <li key={r[labelKey]}>
          <div className="flex items-baseline justify-between gap-3 text-xs">
            <span className="truncate text-slate-700">{r[labelKey]}</span>
            <span className="shrink-0 font-mono text-slate-500">
              {fmt.number(Math.round(r[valueKey]))}
              {unit}
            </span>
          </div>
          <div className="mt-1 h-2 w-full overflow-hidden rounded-full bg-slate-100">
            <div
              className="h-2 rounded-full transition-all"
              style={{
                width: `${(r[valueKey] / max) * 100}%`,
                background: colorFor(r[labelKey]),
              }}
            />
          </div>
        </li>
      ))}
    </ul>
  )
}

export default function Analytics() {
  const npt = useApi(() => api.npt(), [])
  const fields = useApi(() => api.fieldSummary(), [])
  const clusters = useApi(() => api.clusters({ radius_km: 6, min_wells: 3 }), [])

  return (
    <div className="space-y-4">
      <Async query={npt}>
        {(d) => (
          <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
            <Stat label="Events on record" value={fmt.number(d.total_events)} />
            <Stat
              label="Total NPT"
              value={fmt.number(Math.round(d.total_npt_hours))}
              unit="hrs"
              tone="warn"
            />
            <Stat
              label="Equivalent rig days"
              value={fmt.number(Math.round(d.total_npt_hours / 24))}
              hint="at 24 hrs per day"
              tone="warn"
            />
            <Stat
              label="Most frequent hazard"
              value={d.by_event_type[0]?.label?.split(' / ')[0] ?? '—'}
              hint={`${fmt.number(d.by_event_type[0]?.count ?? 0)} occurrences`}
              tone="danger"
            />
          </div>
        )}
      </Async>

      <div className="grid gap-4 lg:grid-cols-2">
        <Card title="Non-productive time by hazard" subtitle="Across every well in the knowledge base">
          <Async query={npt}>
            {(d) => (
              <BarList
                rows={d.by_event_type}
                labelKey="label"
                valueKey="npt_hours"
                unit=" h"
                colorFor={(label) => {
                  const row = d.by_event_type.find((r) => r.label === label)
                  return eventColor(row?.event_type)
                }}
              />
            )}
          </Async>
        </Card>

        <Card title="Non-productive time by formation" subtitle="Where the section costs rig time">
          <Async query={npt}>
            {(d) => (
              <BarList
                rows={d.by_formation}
                labelKey="formation"
                valueKey="npt_hours"
                unit=" h"
                colorFor={formationColor}
              />
            )}
          </Async>
        </Card>
      </div>

      <Card
        title="Recurring hazard zones"
        subtitle="Clusters found from the extracted event record alone, with no prior knowledge of where trouble areas are"
        actions={<Radar className="h-4 w-4 text-oil-700" />}
        bodyClass="p-0"
      >
        <Async query={clusters} empty="No clusters found">
          {(rows) =>
            rows.length === 0 ? (
              <p className="p-6 text-center text-sm text-slate-500">
                No hazard recurred across enough wells to form a cluster.
              </p>
            ) : (
              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="table-head">
                      <th className="px-4 py-2 text-left">Hazard</th>
                      <th className="px-4 py-2 text-left">Formation</th>
                      <th className="px-4 py-2 text-left">Area</th>
                      <th className="px-4 py-2 text-right">Wells</th>
                      <th className="px-4 py-2 text-right">Extent</th>
                      <th className="px-4 py-2 text-right">Depth range</th>
                      <th className="px-4 py-2 text-right">NPT</th>
                      <th className="px-4 py-2 text-left">Severity</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-slate-100">
                    {rows.map((c) => (
                      <tr key={`${c.event_type}-${c.formation}-${c.centre_lat}`} className="hover:bg-slate-50">
                        <td className="px-4 py-2">
                          <span className="flex items-center gap-2">
                            <span
                              className="h-2.5 w-2.5 shrink-0 rounded-full"
                              style={{ background: eventColor(c.event_type) }}
                            />
                            {c.label}
                          </span>
                        </td>
                        <td className="px-4 py-2">
                          <span className="flex items-center gap-1.5">
                            <span
                              className="h-4 w-1.5 rounded"
                              style={{ background: formationColor(c.formation) }}
                            />
                            <span className="text-slate-600">{c.formation}</span>
                          </span>
                        </td>
                        <td className="px-4 py-2 text-slate-600">{c.fields.join(', ')}</td>
                        <td className="px-4 py-2 text-right font-semibold">{c.well_count}</td>
                        <td className="px-4 py-2 text-right font-mono text-xs">{fmt.km(c.extent_km)}</td>
                        <td className="px-4 py-2 text-right font-mono text-xs text-slate-600">
                          {c.tvd_range_m
                            ? `${Math.round(c.tvd_range_m[0]).toLocaleString('en-IN')}–${Math.round(
                                c.tvd_range_m[1],
                              ).toLocaleString('en-IN')} m`
                            : '—'}
                        </td>
                        <td className="px-4 py-2 text-right text-xs text-amber-700">
                          {fmt.hours(c.npt_hours)}
                        </td>
                        <td className="px-4 py-2">
                          <div className="w-24">
                            <ScoreBar
                              value={c.mean_severity / 5}
                              color={c.mean_severity >= 4 ? 'bg-rose-500' : 'bg-amber-500'}
                              height="h-1.5"
                            />
                            <span className="text-[10px] text-slate-500">
                              mean {c.mean_severity.toFixed(1)}/5 · max {c.max_severity}
                            </span>
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

      <Card title="By operating area" bodyClass="p-0">
        <Async query={fields}>
          {(rows) => (
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="table-head">
                    <th className="px-4 py-2 text-left">Field</th>
                    <th className="px-4 py-2 text-right">Wells</th>
                    <th className="px-4 py-2 text-right">Drilling now</th>
                    <th className="px-4 py-2 text-right">Events</th>
                    <th className="px-4 py-2 text-right">Total NPT</th>
                    <th className="px-4 py-2 text-right">NPT per well</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-100">
                  {rows.map((f) => (
                    <tr key={f.field_code} className="hover:bg-slate-50">
                      <td className="px-4 py-2 font-medium text-slate-800">{f.field_name}</td>
                      <td className="px-4 py-2 text-right">{f.wells}</td>
                      <td className="px-4 py-2 text-right">
                        {f.drilling > 0 ? (
                          <Chip className="bg-emerald-100 text-emerald-800 ring-emerald-300">
                            {f.drilling}
                          </Chip>
                        ) : (
                          <span className="text-slate-400">—</span>
                        )}
                      </td>
                      <td className="px-4 py-2 text-right">{f.events}</td>
                      <td className="px-4 py-2 text-right text-amber-700">{fmt.hours(f.npt_hours)}</td>
                      <td className="px-4 py-2 text-right font-mono text-xs">
                        {fmt.hours(f.npt_hours_per_well)}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Async>
      </Card>
    </div>
  )
}
