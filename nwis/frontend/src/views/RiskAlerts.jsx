import { ShieldCheck, Telescope } from 'lucide-react'
import { api } from '../api'
import { bandStyle, fmt, formationColor } from '../format'
import { Async, Card, Chip, Stat, useApi } from '../components/ui'
import AlertCard from '../components/AlertCard'
import DepthTrack from '../components/DepthTrack'

export default function RiskAlerts({ wellId, settings, onSettings, onOpenEvidence,
                                     liveRisk, liveFrame }) {
  const detail = useApi(() => api.well(wellId), [wellId])
  const fetched = useApi(
    () => api.risk(wellId, { lookahead_m: settings.lookahead, radius_km: settings.radius }),
    [wellId, settings.lookahead, settings.radius],
  )

  // While the eRTMAC replay is running the server pushes a freshly computed
  // look-ahead for the simulated bit depth. That is more current than
  // anything this view could fetch, so it wins.
  const live = liveRisk && liveRisk.well_id === wellId ? liveRisk : null
  const risk = live
    ? { ...fetched, data: live, loading: false, error: null }
    : fetched

  return (
    <div className="grid gap-4 xl:grid-cols-4">
      <div className="space-y-4 xl:col-span-3">
        <Async query={risk}>
          {(r) => {
            const counts = r.alerts.reduce((acc, a) => {
              acc[a.risk_band] = (acc[a.risk_band] ?? 0) + 1
              return acc
            }, {})
            return (
              <>
                {live && (
                  <div className="flex flex-wrap items-center gap-3 rounded-lg border border-emerald-200
                                  bg-emerald-50 px-4 py-2.5 text-sm text-emerald-900">
                    <span className="flex h-2 w-2 shrink-0 animate-pulse rounded-full bg-emerald-500" />
                    <span className="font-medium">Live from the eRTMAC replay</span>
                    <span className="text-emerald-700">
                      bit at {fmt.depth(r.bit_md_m)} MD — alerts recomputed as it advances
                    </span>
                    {liveFrame && (
                      <span className="ml-auto font-mono text-xs text-emerald-700">
                        ROP {liveFrame.rop_m_hr?.toFixed(1)} m/hr · MW{' '}
                        {liveFrame.mud_weight_sg?.toFixed(2)} sg · torque{' '}
                        {liveFrame.torque_knm?.toFixed(1)} kNm
                      </span>
                    )}
                  </div>
                )}

                <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
                  <Stat
                    label="Interval ahead"
                    value={`${fmt.depth(r.window_md[0])}`}
                    hint={`to ${fmt.depth(r.window_md[1])} MD`}
                  />
                  <Stat
                    label="High risk"
                    value={counts.High ?? 0}
                    tone={counts.High ? 'danger' : 'good'}
                    hint="alerts in this interval"
                  />
                  <Stat label="Medium risk" value={counts.Medium ?? 0} tone="warn" hint="alerts" />
                  <Stat
                    label="Offsets used"
                    value={`${r.offsets_used} / ${r.offsets_considered}`}
                    hint={`within ${settings.radius} km`}
                  />
                </div>

                {r.alerts.length === 0 ? (
                  <Card bodyClass="p-8">
                    <div className="flex flex-col items-center gap-3 text-center">
                      <ShieldCheck className="h-10 w-10 text-emerald-500" />
                      <p className="text-lg font-semibold text-slate-800">Nothing flagged ahead</p>
                      <p className="max-w-md text-sm text-slate-500">
                        No relevant offset well recorded trouble in the{' '}
                        {fmt.depth(r.window_md[0])} &ndash; {fmt.depth(r.window_md[1])} MD interval.
                        Widen the look-ahead or the search radius to check further.
                      </p>
                    </div>
                  </Card>
                ) : (
                  <div className="space-y-3">
                    {r.alerts.map((a, i) => (
                      <AlertCard
                        key={a.alert_id}
                        alert={a}
                        onOpenEvidence={onOpenEvidence}
                        defaultOpen={i === 0}
                      />
                    ))}
                  </div>
                )}
              </>
            )
          }}
        </Async>
      </div>

      <div className="space-y-4">
        <Card title="Look-ahead" bodyClass="p-4 space-y-4">
          <div>
            <div className="flex items-baseline justify-between text-xs">
              <label htmlFor="ahead" className="text-slate-600">
                Distance ahead of the bit
              </label>
              <span className="font-mono text-slate-700">{settings.lookahead} m</span>
            </div>
            <input
              id="ahead"
              type="range"
              min="50"
              max="1000"
              step="50"
              value={settings.lookahead}
              onChange={(e) => onSettings({ ...settings, lookahead: Number(e.target.value) })}
              className="mt-1 w-full accent-oil-600"
            />
          </div>
          <div>
            <div className="flex items-baseline justify-between text-xs">
              <label htmlFor="risk-radius" className="text-slate-600">
                Offset search radius
              </label>
              <span className="font-mono text-slate-700">{settings.radius} km</span>
            </div>
            <input
              id="risk-radius"
              type="range"
              min="2"
              max="40"
              step="1"
              value={settings.radius}
              onChange={(e) => onSettings({ ...settings, radius: Number(e.target.value) })}
              className="mt-1 w-full accent-oil-600"
            />
          </div>
        </Card>

        <Card title="Where the alerts sit" bodyClass="p-3">
          <Async query={detail}>
            {(d) => (
              <div className="flex justify-center">
                <DepthTrack
                  tops={d.formation_tops}
                  events={d.events}
                  tdTvd={d.well.td_tvd_m}
                  bitTvd={d.bit_tvd_m}
                  lookaheadTo={risk.data?.window_tvd?.[1]}
                  alerts={risk.data?.alerts ?? []}
                  height={430}
                  width={240}
                />
              </div>
            )}
          </Async>
          <p className="mt-2 text-[11px] leading-snug text-slate-500">
            Red arrow is the bit. The blue band is the interval being analysed; coloured bars to its
            left are the predicted alert intervals.
          </p>
        </Card>

        <Card title="Formations in the interval" bodyClass="p-0">
          <Async query={risk}>
            {(r) => (
              <ul className="divide-y divide-slate-100">
                {r.formations_ahead.map((f) => (
                  <li key={f.formation} className="px-4 py-2.5">
                    <div className="flex items-center gap-2">
                      <span
                        className="h-5 w-2 rounded"
                        style={{ background: formationColor(f.formation) }}
                      />
                      <span className="flex-1 truncate text-sm font-medium text-slate-800">
                        {f.formation}
                      </span>
                      <Chip
                        className={
                          f.source === 'prognosis'
                            ? 'bg-amber-100 text-amber-900 ring-amber-300'
                            : 'bg-slate-100 text-slate-600 ring-slate-300'
                        }
                      >
                        {f.source}
                      </Chip>
                    </div>
                    {f.pore_pressure_sg && (
                      <p className="mt-1 text-[11px] text-slate-500">
                        Pore pressure ~{f.pore_pressure_sg} sg &middot; fracture gradient ~
                        {f.frac_gradient_sg} sg
                      </p>
                    )}
                  </li>
                ))}
              </ul>
            )}
          </Async>
          <p className="border-t border-slate-100 px-4 py-2 text-[11px] leading-snug text-slate-500">
            <Telescope className="mr-1 inline h-3 w-3" />
            Tops below the bit are a prognosis from the regional structural model, anchored on this
            well&rsquo;s own picks.
          </p>
        </Card>
      </div>
    </div>
  )
}
