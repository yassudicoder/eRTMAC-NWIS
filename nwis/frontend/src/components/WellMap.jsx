import { useEffect, useMemo } from 'react'
import { CircleMarker, MapContainer, Polyline, Popup, TileLayer, Circle, useMap } from 'react-leaflet'
import { bandStyle, fmt } from '../format'

/**
 * Map of the current well and its ranked offsets.
 *
 * Offsets are coloured by relevance band and sized by relevance score, so the
 * useful wells stand out from the merely close ones - which is the whole
 * point the system is making.
 */

const BASE_LAYERS = {
  street: {
    url: 'https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png',
    attribution: '&copy; OpenStreetMap contributors',
  },
  terrain: {
    url: 'https://{s}.tile.opentopomap.org/{z}/{x}/{y}.png',
    attribution: '&copy; OpenTopoMap, &copy; OpenStreetMap contributors',
  },
}

function FitToWells({ points, centre, radiusKm }) {
  const map = useMap()
  useEffect(() => {
    if (!points.length) return
    const lats = points.map((p) => p[0])
    const lons = points.map((p) => p[1])
    // Include the search radius in the fit. It is the graphic that expresses
    // "relative to the active well", and fitting only to the markers pushed
    // it off screen entirely at the default radius.
    if (centre && radiusKm) {
      const dLat = radiusKm / 110.574
      const dLon = radiusKm / (111.32 * Math.cos((centre[0] * Math.PI) / 180))
      lats.push(centre[0] - dLat, centre[0] + dLat)
      lons.push(centre[1] - dLon, centre[1] + dLon)
    }
    map.fitBounds(
      [
        [Math.min(...lats), Math.min(...lons)],
        [Math.max(...lats), Math.max(...lons)],
      ],
      { padding: [30, 30], maxZoom: 13 },
    )
  }, [map, JSON.stringify(points), JSON.stringify(centre), radiusKm])
  return null
}

export default function WellMap({
  currentWell,
  offsets = [],
  radiusKm,
  selectedId,
  onSelect,
  layer = 'street',
  showRadius = true,
}) {
  const centre = currentWell
    ? [currentWell.surface_lat, currentWell.surface_lon]
    : [27.36, 95.32]

  const points = useMemo(() => {
    const list = currentWell ? [[currentWell.surface_lat, currentWell.surface_lon]] : []
    offsets.forEach((o) => list.push([o.surface_lat, o.surface_lon]))
    return list
  }, [currentWell, offsets])

  const base = BASE_LAYERS[layer] ?? BASE_LAYERS.street

  return (
    <MapContainer center={centre} zoom={11} scrollWheelZoom className="h-full w-full">
      <TileLayer url={base.url} attribution={base.attribution} />
      <FitToWells points={points} centre={currentWell ? centre : null} radiusKm={radiusKm} />

      {showRadius && currentWell && radiusKm ? (
        <Circle
          center={centre}
          radius={radiusKm * 1000}
          pathOptions={{ color: '#2047e0', weight: 1, fillOpacity: 0.04, dashArray: '6 6' }}
        />
      ) : null}

      {offsets.map((o) => {
        const style = bandStyle(o.relevance_band)
        const selected = selectedId === o.well_id
        return (
          <div key={o.well_id}>
            {currentWell && (
              <Polyline
                positions={[centre, [o.surface_lat, o.surface_lon]]}
                pathOptions={{
                  color: style.hex,
                  weight: selected ? 3 : 1,
                  opacity: selected ? 0.9 : 0.25,
                }}
              />
            )}
            <CircleMarker
              center={[o.surface_lat, o.surface_lon]}
              radius={6 + (o.relevance_score ?? 0) * 8}
              pathOptions={{
                color: selected ? '#0f172a' : style.hex,
                weight: selected ? 3 : 1.5,
                fillColor: style.hex,
                fillOpacity: 0.75,
              }}
              eventHandlers={{ click: () => onSelect?.(o.well_id) }}
            >
              <Popup>
                <div className="space-y-1 text-xs">
                  <p className="text-sm font-semibold text-slate-900">{o.well_name}</p>
                  <p className="text-slate-500">
                    {o.field_name} field &middot; {o.well_type} &middot; {fmt.year(o.completion_date)}
                  </p>
                  <p>
                    Relevance <b>{fmt.score(o.relevance_score)}</b> ({o.relevance_band}) &middot;{' '}
                    {fmt.km(o.distance_km)} {o.bearing}
                  </p>
                  <p>
                    TD {fmt.depth(o.td_md_m)} &middot; {o.event_count} events &middot;{' '}
                    {fmt.hours(o.npt_hours)} NPT
                  </p>
                  {o.supports?.slice(0, 2).map((s) => (
                    <p key={s} className="text-emerald-700">
                      + {s}
                    </p>
                  ))}
                </div>
              </Popup>
            </CircleMarker>
          </div>
        )
      })}

      {currentWell && (
        <>
          <CircleMarker
            center={centre}
            radius={11}
            pathOptions={{ color: '#ffffff', weight: 3, fillColor: '#dc2626', fillOpacity: 1 }}
          >
            <Popup>
              <div className="space-y-1 text-xs">
                <p className="text-sm font-semibold text-slate-900">{currentWell.well_name}</p>
                <p className="text-slate-500">
                  {currentWell.field_name} field &middot; {currentWell.status}
                </p>
                <p>
                  Bit at{' '}
                  <b>{fmt.depth(currentWell.current_bit_md_m ?? currentWell.td_md_m)}</b> MD
                </p>
                <p>Target: {currentWell.target_formation}</p>
              </div>
            </Popup>
          </CircleMarker>
          {currentWell.bottom_lat !== currentWell.surface_lat && (
            <Polyline
              positions={[centre, [currentWell.bottom_lat, currentWell.bottom_lon]]}
              pathOptions={{ color: '#dc2626', weight: 2, dashArray: '4 4' }}
            />
          )}
        </>
      )}
    </MapContainer>
  )
}
