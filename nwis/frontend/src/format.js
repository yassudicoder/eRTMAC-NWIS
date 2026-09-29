/** Shared formatting and colour conventions. */

export const fmt = {
  depth: (v) => (v === null || v === undefined ? '-' : `${Math.round(v).toLocaleString('en-IN')} m`),
  depth1: (v) => (v === null || v === undefined ? '-' : `${v.toFixed(1)} m`),
  km: (v) => (v === null || v === undefined ? '-' : `${v.toFixed(1)} km`),
  pct: (v) => (v === null || v === undefined ? '-' : `${Math.round(v * 100)}%`),
  score: (v) => (v === null || v === undefined ? '-' : v.toFixed(2)),
  hours: (v) => (v === null || v === undefined ? '-' : `${Math.round(v).toLocaleString('en-IN')} h`),
  number: (v) => (v === null || v === undefined ? '-' : v.toLocaleString('en-IN')),
  date: (iso) => {
    if (!iso) return '-'
    const [y, m, d] = iso.split('-')
    return `${d}-${m}-${y}`
  },
  year: (iso) => (iso ? iso.slice(0, 4) : '-'),
}

/** Risk and relevance bands share one colour language across the whole UI. */
export const BAND_STYLES = {
  High: {
    chip: 'bg-rose-100 text-rose-800 ring-rose-300',
    dot: 'bg-rose-500',
    bar: 'bg-rose-500',
    border: 'border-l-rose-500',
    hex: '#e11d48',
  },
  Medium: {
    chip: 'bg-amber-100 text-amber-900 ring-amber-300',
    dot: 'bg-amber-500',
    bar: 'bg-amber-500',
    border: 'border-l-amber-500',
    hex: '#f59e0b',
  },
  Low: {
    chip: 'bg-emerald-100 text-emerald-800 ring-emerald-300',
    dot: 'bg-emerald-500',
    bar: 'bg-emerald-500',
    border: 'border-l-emerald-500',
    hex: '#10b981',
  },
}

export const bandStyle = (band) => BAND_STYLES[band] ?? BAND_STYLES.Low

/** Severity 1..5 rendered as a consistent colour ramp. */
export const SEVERITY_HEX = ['#94a3b8', '#38bdf8', '#facc15', '#fb923c', '#ef4444', '#b91c1c']

export function severityColor(severity) {
  return SEVERITY_HEX[Math.max(0, Math.min(5, severity ?? 0))]
}

/**
 * Stable colour per formation, so the same unit is the same colour on the
 * map, the depth tracks and the correlation panel.
 */
export const FORMATION_COLORS = {
  Dihing: '#d6d3d1',
  Namsang: '#fde68a',
  'Girujan Clay': '#a3b18a',
  'Tipam Sandstone': '#f0a868',
  'Barail Coal Shale': '#57534e',
  'Barail Arenaceous': '#c2703d',
  'Kopili Shale': '#6b7280',
  'Sylhet Limestone': '#7dd3fc',
  Langpar: '#a5b4fc',
  Basement: '#831843',
}

export const formationColor = (name) => FORMATION_COLORS[name] ?? '#cbd5e1'

export const EVENT_ICON_COLORS = {
  MUD_LOSS: '#0ea5e9',
  STUCK_PIPE: '#a855f7',
  KICK: '#ef4444',
  WELLBORE_INSTABILITY: '#f97316',
  TIGHT_HOLE: '#eab308',
  HIGH_TORQUE_DRAG: '#14b8a6',
  BIT_BALLING: '#8b5cf6',
  WASHOUT: '#64748b',
  LOW_ROP: '#78716c',
  EQUIPMENT_FAILURE: '#475569',
}

export const eventColor = (type) => EVENT_ICON_COLORS[type] ?? '#64748b'
