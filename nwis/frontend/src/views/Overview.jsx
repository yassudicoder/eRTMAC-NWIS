import { Activity, Database, FileText, Layers, TriangleAlert } from 'lucide-react'
import { api } from '../api'
import { fmt, formationColor } from '../format'
import { Async, Card, Chip, ScoreBar, Stat, useApi } from '../components/ui'
import WellMap from '../components/WellMap'
import DepthTrack from '../components/DepthTrack'
import AlertCard from '../components/AlertCard'
import { bandStyle } from '../format'

export default function Overview({ wellId, settings, onOpenEvidence, onNavigate }) {
  const detail = useApi(() => api.well(wellId), [wellId])
  const stats = useApi(() => api.stats(), [])
  const risk = useApi(
    () => api.risk(wellId, { lookahead_m: settings.lookahead, radius_km: settings.radius }),
    [wellId, settings.lookahead, settings.radius],
  )
  const offsets = useApi(
    () => api.offsets(wellId, { radius_km: settings.radius, limit: 8 }),
    [wellId, settings.radius],
  )

  return (
    <div className="space-y-4">
      <Async query={stats}>
        {(s) => (
          <div className="grid grid-cols-2 gap-3 lg:grid-cols-5">
            <Stat label="Wells in knowledge base" value={fmt.number(s.wells)} hint={`${s.fields} operating areas`} />
            <Stat
              label="Reports ingested"
              value={fmt.number(s.documents)}
              hint={`${fmt.number(s.document_pages)} pages read`}
            />
            <Stat
              label="Events extracted"
              value={fmt.number(s.events)}
              hint={`${fmt.number(s.citations)} source references`}
            />
            <Stat
              label="NPT on record"
              value={fmt.number(Math.round(s.total_npt_hours))}
              unit="hrs"
              tone="warn"
              hint="across all wells"
            />
            <Stat
              label="Flagged for review"
              value={fmt.number(s.events_needing_review)}
              tone={s.events_needing_review > 0 ? 'warn' : 'good'}
              hint="low-confidence extractions"
            />
          </div>
        )}
      </Async>

      <Async query={detail}>
        {(d) => {
          const well = d.well
          const plannedTd = well.planned_td_md_m ?? well.td_md_m
          const alerts = risk.data?.alerts ?? []
          const highCount = alerts.filter((a) => a.risk_band === 'High').length

          return (
            <>
              <Card
                title={`${well.well_name} — ${well.field_name} field`}
                subtitle={`${well.purpose} · ${well.well_type} · rig ${well.rig} · spudded ${fmt.date(well.spud_date)}`}
                actions={
                  <Chip
                    className={
                      well.status === 'Drilling'
                        ? 'bg-emerald-100 text-emerald-800 ring-emerald-300'
                        : 'bg-slate-100 text-slate-700 ring-slate-300'
                    }
                  >
                    <span
                      className={`h-1.5 w-1.5 rounded-full ${
                        well.status === 'Drilling' ? 'animate-pulse bg-emerald-500' : 'bg-slate-400'
                      }`}
                    />
                    {well.status}
                  </Chip>
                }
              >
                <div className="grid gap-4 lg:grid-cols-4">
                  <div className="space-y-3 lg:col-span-1">
                    <div>
                      <div className="flex items-baseline justify-between text-xs text-slate-500">
                        <span>Bit depth</span>
                        <span className="font-mono text-slate-700">
                          {fmt.depth(d.bit_md_m)} / {fmt.depth(plannedTd)} MD
                        </span>
                      </div>
                      <div className="mt-1">
                        <ScoreBar value={d.progress_pct / 100} color="bg-oil-600" height="h-2" />
                      </div>
                      <p className="mt-1 text-xs text-slate-500">
                        {d.progress_pct}% of planned depth &middot; {fmt.depth(d.bit_tvd_m)} TVD
                      </p>
                    </div>

                    <dl className="space-y-1.5 text-xs">
                      {[
                        ['Target formation', well.target_formation],
                        ['Mud system', well.mud_system],
                        ['Max inclination', `${well.max_inclination_deg}°`],
                        ['Surface location', `${well.surface_lat}, ${well.surface_lon}`],
                        ['Events on this well', `${d.events.length}`],
                        ['Reports held', `${d.documents.length}`],
                      ].map(([k, v]) => (
                        <div key={k} className="flex justify-between gap-2">
                          <dt className="text-slate-500">{k}</dt>
                          <dd className="truncate text-right font-medium text-slate-800">{v}</dd>
                        </div>
                      ))}
                    </dl>
                  </div>

                  <div className="lg:col-span-1">
                    <DepthTrack
                      label="Section"
                      tops={d.formation_tops}
                      events={d.events}
                      tdTvd={well.td_tvd_m}
                      bitTvd={d.bit_tvd_m}
                      lookaheadTo={risk.data?.window_tvd?.[1]}
                      alerts={alerts}
                      height={340}
                      width={230}
                    />
                  </div>

                  <div className="h-[340px] overflow-hidden rounded-lg border border-slate-200 lg:col-span-2">
                    <WellMap
                      currentWell={well}
                      offsets={offsets.data?.offsets ?? []}
                      radiusKm={settings.radius}
                      onSelect={() => onNavigate('offsets')}
                    />
                  </div>
                </div>
              </Card>

              <div className="grid gap-4 lg:grid-cols-3">
                <Card
                  className="lg:col-span-2"
                  title="Risk ahead of the bit"
                  subtitle={
                    risk.data
                      ? `${fmt.depth(risk.data.window_md[0])} – ${fmt.depth(
                          risk.data.window_md[1],
                        )} MD · ${risk.data.offsets_used} relevant offsets used`
                      : undefined
                  }
                  actions={
                    highCount > 0 ? (
                      <Chip className={bandStyle('High').chip}>
                        <TriangleAlert className="h-3 w-3" /> {highCount} high
                      </Chip>
                    ) : null
                  }
                  bodyClass="p-4 space-y-3"
                >
                  <Async query={risk} empty="No look-ahead available">
                    {(r) =>
                      r.alerts.length === 0 ? (
                        <p className="py-6 text-center text-sm text-emerald-700">
                          No offset-well evidence of trouble in the interval ahead.
                        </p>
                      ) : (
                        <>
                          {r.alerts.slice(0, 2).map((a) => (
                            <AlertCard key={a.alert_id} alert={a} onOpenEvidence={onOpenEvidence} />
                          ))}
                          {r.alerts.length > 2 && (
                            <button
                              type="button"
                              className="btn btn-primary w-full justify-center"
                              onClick={() => onNavigate('risk')}
                            >
                              View all {r.alerts.length} alerts
                            </button>
                          )}
                        </>
                      )
                    }
                  </Async>
                </Card>

                <div className="space-y-4">
                  <Card title="Formations ahead" bodyClass="p-0">
                    <Async query={risk}>
                      {(r) =>
                        r.formations_ahead.length === 0 ? (
                          <p className="p-4 text-sm text-slate-500">No formation tops in this interval.</p>
                        ) : (
                          <ul className="divide-y divide-slate-100">
                            {r.formations_ahead.map((f) => (
                              <li key={f.formation} className="flex items-center gap-3 px-4 py-2.5">
                                <span
                                  className="h-7 w-2 shrink-0 rounded"
                                  style={{ background: formationColor(f.formation) }}
                                />
                                <div className="min-w-0 flex-1">
                                  <p className="truncate text-sm font-medium text-slate-800">{f.formation}</p>
                                  <p className="truncate text-xs text-slate-500">{f.lithology}</p>
                                </div>
                                <div className="shrink-0 text-right">
                                  <p className="text-xs font-mono text-slate-700">
                                    {f.top_md_m ? fmt.depth(f.top_md_m) : 'drilling'}
                                  </p>
                                  <p className="text-[10px] uppercase tracking-wide text-slate-400">
                                    {f.source}
                                  </p>
                                </div>
                              </li>
                            ))}
                          </ul>
                        )
                      }
                    </Async>
                  </Card>

                  <Card title="Live signals" bodyClass="p-0">
                    <Async query={risk}>
                      {(r) =>
                        r.signals.length === 0 ? (
                          <p className="p-4 text-sm text-slate-500">
                            Drilling parameters are steady — nothing trending.
                          </p>
                        ) : (
                          <ul className="divide-y divide-slate-100">
                            {r.signals.map((s) => (
                              <li key={s.key} className="px-4 py-2.5">
                                <div className="flex items-center justify-between gap-2">
                                  <p className="flex items-center gap-1.5 text-sm font-medium text-slate-800">
                                    <Activity className="h-3.5 w-3.5 text-violet-600" />
                                    {s.label}
                                  </p>
                                  <span className="font-mono text-xs text-slate-500">
                                    {s.value} {s.unit}
                                  </span>
                                </div>
                                <div className="mt-1.5">
                                  <ScoreBar value={s.strength} color="bg-violet-500" height="h-1" />
                                </div>
                                <p className="mt-1 text-[11px] leading-snug text-slate-500">{s.note}</p>
                              </li>
                            ))}
                          </ul>
                        )
                      }
                    </Async>
                  </Card>
                </div>
              </div>

              <Card
                title="Pipeline"
                subtitle="How the numbers above were produced"
                bodyClass="p-4"
              >
                <ol className="grid gap-3 text-sm md:grid-cols-5">
                  {[
                    [Database, 'Ingest', 'WCR, DDR, mud logs and eRTMAC parameter logs'],
                    [FileText, 'Extract', 'Rule-based NLP pulls events, depths and magnitudes'],
                    [Layers, 'Correlate', 'Shared formation tops tie wells to a common depth frame'],
                    [Activity, 'Rank', 'Offsets scored on six similarity dimensions'],
                    [TriangleAlert, 'Alert', 'Interval ahead of the bit, with citations'],
                  ].map(([Icon, title, body], i) => (
                    <li key={title} className="rounded-lg border border-slate-200 bg-slate-50 p-3">
                      <p className="flex items-center gap-2 font-semibold text-slate-800">
                        <span className="flex h-6 w-6 items-center justify-center rounded-full bg-oil-700 text-xs font-bold text-white">
                          {i + 1}
                        </span>
                        <Icon className="h-4 w-4 text-oil-700" />
                        {title}
                      </p>
                      <p className="mt-1.5 text-xs leading-snug text-slate-600">{body}</p>
                    </li>
                  ))}
                </ol>
              </Card>
            </>
          )
        }}
      </Async>
    </div>
  )
}
