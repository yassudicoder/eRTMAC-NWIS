import { useState } from 'react'
import { Map as MapIcon, Mountain } from 'lucide-react'
import { api } from '../api'
import { fmt } from '../format'
import { Async, Card, useApi } from '../components/ui'
import WellMap from '../components/WellMap'
import OffsetList from '../components/OffsetList'
import WeightSliders, { DEFAULT_WEIGHTS } from '../components/WeightSliders'

/**
 * "Which nearby wells actually matter, and why?"
 *
 * The radius filter is only candidate selection. The ranking underneath it is
 * what the system is really claiming, which is why the weights are exposed
 * and every row can be opened to show its arithmetic.
 */
export default function NearbyWells({ wellId, settings, onSettings, onCompare }) {
  const [weights, setWeights] = useState(DEFAULT_WEIGHTS)
  const [selected, setSelected] = useState(null)
  const [layer, setLayer] = useState('street')
  const [limit, setLimit] = useState(15)

  const detail = useApi(() => api.well(wellId), [wellId])
  const offsets = useApi(
    () => api.offsets(wellId, { radius_km: settings.radius, limit, weights }),
    [wellId, settings.radius, limit, JSON.stringify(weights)],
  )

  const rows = offsets.data?.offsets ?? []
  const bands = rows.reduce((acc, o) => {
    acc[o.relevance_band] = (acc[o.relevance_band] ?? 0) + 1
    return acc
  }, {})

  return (
    <div className="grid gap-4 xl:grid-cols-3">
      <div className="space-y-4 xl:col-span-2">
        <Card
          title="Offset wells"
          subtitle={
            offsets.data
              ? `${
                  offsets.data.total_in_radius > rows.length
                    ? `Showing the ${rows.length} most relevant of ${offsets.data.total_in_radius}`
                    : `${rows.length}`
                } wells within ${settings.radius} km · ${bands.High ?? 0} high, ${
                  bands.Medium ?? 0
                } medium, ${bands.Low ?? 0} low relevance`
              : undefined
          }
          actions={
            <div className="flex items-center gap-2">
              <button
                type="button"
                className={`btn !px-2 !py-1 ${layer === 'street' ? 'btn-primary' : ''}`}
                onClick={() => setLayer('street')}
                title="Street map"
              >
                <MapIcon className="h-3.5 w-3.5" />
              </button>
              <button
                type="button"
                className={`btn !px-2 !py-1 ${layer === 'terrain' ? 'btn-primary' : ''}`}
                onClick={() => setLayer('terrain')}
                title="Terrain map"
              >
                <Mountain className="h-3.5 w-3.5" />
              </button>
            </div>
          }
          bodyClass="p-0"
        >
          <div className="h-[420px] border-b border-slate-200">
            <WellMap
              currentWell={detail.data?.well}
              offsets={rows}
              radiusKm={settings.radius}
              selectedId={selected}
              onSelect={setSelected}
              layer={layer}
            />
          </div>
          <div className="flex flex-wrap items-center gap-4 px-4 py-2 text-xs text-slate-500">
            <span className="flex items-center gap-1.5">
              <span className="h-2.5 w-2.5 rounded-full bg-red-600 ring-2 ring-white" />
              Current well
            </span>
            {['High', 'Medium', 'Low'].map((band) => (
              <span key={band} className="flex items-center gap-1.5">
                <span
                  className="h-2.5 w-2.5 rounded-full"
                  style={{
                    background: band === 'High' ? '#e11d48' : band === 'Medium' ? '#f59e0b' : '#10b981',
                  }}
                />
                {band} relevance
              </span>
            ))}
            <span className="ml-auto">Marker size scales with relevance score</span>
          </div>
        </Card>

        <Card title="Ranked by combined similarity" bodyClass="p-0">
          <Async query={offsets} empty="No offset wells">
            {() => (
              <OffsetList
                offsets={rows}
                selectedId={selected}
                onSelect={setSelected}
                onCompare={onCompare}
              />
            )}
          </Async>
        </Card>
      </div>

      <div className="space-y-4">
        <Card title="Search" bodyClass="p-4 space-y-4">
          <div>
            <div className="flex items-baseline justify-between text-xs">
              <label htmlFor="radius" className="text-slate-600">
                Candidate radius
              </label>
              <span className="font-mono text-slate-700">{settings.radius} km</span>
            </div>
            <input
              id="radius"
              type="range"
              min="2"
              max="40"
              step="1"
              value={settings.radius}
              onChange={(e) => onSettings({ ...settings, radius: Number(e.target.value) })}
              className="mt-1 w-full accent-oil-600"
            />
            <div className="mt-1 flex gap-1">
              {[5, 10, 20].map((r) => (
                <button
                  key={r}
                  type="button"
                  className={`btn !px-2 !py-0.5 text-xs ${settings.radius === r ? 'btn-primary' : ''}`}
                  onClick={() => onSettings({ ...settings, radius: r })}
                >
                  {r} km
                </button>
              ))}
            </div>
          </div>

          <div>
            <div className="flex items-baseline justify-between text-xs">
              <label htmlFor="limit" className="text-slate-600">
                Wells shown
              </label>
              <span className="font-mono text-slate-700">{limit}</span>
            </div>
            <input
              id="limit"
              type="range"
              min="3"
              max="40"
              step="1"
              value={limit}
              onChange={(e) => setLimit(Number(e.target.value))}
              className="mt-1 w-full accent-oil-600"
            />
          </div>
        </Card>

        <Card bodyClass="p-4">
          <WeightSliders
            weights={weights}
            onChange={setWeights}
            onReset={() => setWeights(DEFAULT_WEIGHTS)}
          />
        </Card>

        <Card title="How ranking works" bodyClass="p-4">
          <p className="text-xs leading-relaxed text-slate-600">
            The radius only decides which wells are <em>considered</em>. Each candidate is then
            scored on six dimensions and the weighted total decides the order, so a well 9 km away
            on the same structural block can outrank one 2 km away that stopped above the section
            of interest.
          </p>
          <p className="mt-2 text-xs leading-relaxed text-slate-600">
            Expand any row to see each dimension&rsquo;s score, its weight and its contribution to
            the total.
          </p>
        </Card>

        {selected && (
          <Card title="Selected well" bodyClass="p-4">
            {(() => {
              const o = rows.find((r) => r.well_id === selected)
              if (!o) return <p className="text-sm text-slate-500">Not in the current results.</p>
              return (
                <div className="space-y-2 text-sm">
                  <p className="font-semibold text-slate-900">{o.well_name}</p>
                  <dl className="space-y-1 text-xs">
                    {[
                      ['Relevance', `${fmt.score(o.relevance_score)} (${o.relevance_band})`],
                      ['Distance', `${fmt.km(o.distance_km)} ${o.bearing}`],
                      ['Surface distance', fmt.km(o.surface_distance_km)],
                      ['Total depth', `${fmt.depth(o.td_md_m)} MD`],
                      ['Target', o.target_formation],
                      ['Completed', fmt.date(o.completion_date)],
                      ['Events', `${o.event_count} · ${fmt.hours(o.npt_hours)} NPT`],
                      ['Shared formations', o.shared_formations.join(', ') || '—'],
                    ].map(([k, v]) => (
                      <div key={k} className="flex justify-between gap-3">
                        <dt className="shrink-0 text-slate-500">{k}</dt>
                        <dd className="text-right font-medium text-slate-800">{v}</dd>
                      </div>
                    ))}
                  </dl>
                  <button
                    type="button"
                    className="btn btn-primary w-full justify-center"
                    onClick={() => onCompare(o.well_id)}
                  >
                    Correlate with current well
                  </button>
                </div>
              )
            })()}
          </Card>
        )}
      </div>
    </div>
  )
}
