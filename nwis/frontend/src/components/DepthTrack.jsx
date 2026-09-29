import { formationColor, eventColor, fmt } from '../format'

/**
 * A vertical depth track: formation column, drilled interval, events, and the
 * bit with the interval ahead of it.
 *
 * Depth runs downwards, which is how every log and correlation panel in the
 * industry is read. Hand-drawn SVG rather than a chart library, because the
 * axis convention and the formation banding are the whole point.
 */

const PAD_TOP = 18
const PAD_BOTTOM = 14

export default function DepthTrack({
  tops = [],
  events = [],
  tdTvd,
  bitTvd,
  lookaheadTo,
  alerts = [],
  height = 460,
  width = 250,
  label,
  onEventClick,
  depthKey = 'tvd_m',
}) {
  const maxDepth = Math.max(tdTvd ?? 0, lookaheadTo ?? 0, ...tops.map((t) => t.top_tvd_m), 100)
  const plotH = height - PAD_TOP - PAD_BOTTOM
  const y = (depth) => PAD_TOP + (Math.max(0, Math.min(depth, maxDepth)) / maxDepth) * plotH

  const columnX = 54
  const columnW = 34
  const eventX = columnX + columnW + 12

  // Depth axis ticks at a round interval that yields roughly 8 labels.
  const rawStep = maxDepth / 8
  const step = [50, 100, 200, 250, 500, 1000].find((s) => s >= rawStep) ?? 1000
  const ticks = []
  for (let d = 0; d <= maxDepth; d += step) ticks.push(d)

  return (
    <svg width={width} height={height} className="select-none" role="img" aria-label={label}>
      {label && (
        <text x={4} y={12} className="fill-slate-600 text-[10px] font-semibold uppercase">
          {label}
        </text>
      )}

      {/* depth axis */}
      {ticks.map((d) => (
        <g key={d}>
          <line x1={columnX - 5} x2={columnX} y1={y(d)} y2={y(d)} stroke="#cbd5e1" strokeWidth="1" />
          <text x={columnX - 8} y={y(d) + 3} textAnchor="end" className="fill-slate-400 text-[9px] tabular-nums">
            {d.toLocaleString('en-IN')}
          </text>
        </g>
      ))}

      {/* formation column */}
      {tops.map((top, i) => {
        const nextTop = tops[i + 1]?.top_tvd_m ?? Math.max(tdTvd ?? maxDepth, maxDepth)
        const y0 = y(top.top_tvd_m)
        const y1 = y(nextTop)
        const h = Math.max(1, y1 - y0)
        return (
          <g key={`${top.formation}-${i}`}>
            <rect
              x={columnX}
              y={y0}
              width={columnW}
              height={h}
              fill={formationColor(top.formation)}
              stroke="#ffffff"
              strokeWidth="0.5"
            >
              <title>{`${top.formation} - top ${fmt.depth(top.top_tvd_m)} TVD`}</title>
            </rect>
            {h > 13 && (
              <text
                x={columnX + columnW + 4}
                y={y0 + Math.min(h / 2 + 3, 12)}
                className="fill-slate-600 text-[8px]"
              >
                {top.formation}
              </text>
            )}
          </g>
        )
      })}

      {/* the interval ahead of the bit */}
      {bitTvd != null && lookaheadTo != null && lookaheadTo > bitTvd && (
        <rect
          x={columnX - 4}
          y={y(bitTvd)}
          width={columnW + 8}
          height={Math.max(2, y(lookaheadTo) - y(bitTvd))}
          fill="#2563eb"
          opacity="0.16"
        />
      )}

      {/* predicted alert intervals */}
      {alerts.map((a) => {
        const y0 = y(a.predicted_tvd_from)
        const y1 = y(a.predicted_tvd_to)
        const colour = a.risk_band === 'High' ? '#e11d48' : a.risk_band === 'Medium' ? '#f59e0b' : '#10b981'
        return (
          <rect
            key={a.alert_id}
            x={columnX - 9}
            y={y0}
            width={5}
            height={Math.max(3, y1 - y0)}
            fill={colour}
            rx="2"
          >
            <title>{`${a.label} - ${a.risk_band} risk, ${fmt.depth(a.predicted_md_from)}-${fmt.depth(a.predicted_md_to)} MD`}</title>
          </rect>
        )
      })}

      {/* events */}
      {events.map((e, i) => {
        const depth = e[depthKey] ?? e.tvd_m
        if (depth == null) return null
        return (
          <g
            key={e.event_id ?? i}
            transform={`translate(${eventX + 62}, ${y(depth)})`}
            onClick={() => onEventClick?.(e)}
            className={onEventClick ? 'cursor-pointer' : ''}
          >
            <circle r={3 + (e.severity ?? 2) * 0.7} fill={eventColor(e.event_type)} opacity="0.85" />
            <title>
              {`${e.event_type.replace(/_/g, ' ')} - severity ${e.severity}/5 at ${fmt.depth(depth)} TVD`}
            </title>
          </g>
        )
      })}

      {/* bit */}
      {bitTvd != null && (
        <g>
          <line
            x1={columnX - 14}
            x2={columnX + columnW + 66}
            y1={y(bitTvd)}
            y2={y(bitTvd)}
            stroke="#dc2626"
            strokeWidth="1.75"
          />
          <polygon
            points={`${columnX - 14},${y(bitTvd) - 4} ${columnX - 6},${y(bitTvd)} ${columnX - 14},${y(bitTvd) + 4}`}
            fill="#dc2626"
          />
        </g>
      )}

      {/* total depth */}
      {tdTvd != null && (
        <line
          x1={columnX - 6}
          x2={columnX + columnW + 6}
          y1={y(tdTvd)}
          y2={y(tdTvd)}
          stroke="#475569"
          strokeWidth="1.5"
          strokeDasharray="3 2"
        />
      )}
    </svg>
  )
}
