import { RotateCcw, SlidersHorizontal } from 'lucide-react'

export const DEFAULT_WEIGHTS = {
  geology: 0.26,
  depth: 0.14,
  distance: 0.18,
  trajectory: 0.1,
  parameters: 0.12,
  experience: 0.15,
  recency: 0.05,
}

const LABELS = {
  geology: 'Geological similarity',
  depth: 'Depth / interval coverage',
  distance: 'Proximity',
  trajectory: 'Trajectory similarity',
  parameters: 'Drilling parameters',
  experience: 'Historical experience',
  recency: 'Recency',
}

/**
 * Exposes the relevance weights as sliders.
 *
 * Being able to say "show me the ranking if I only care about geology" is
 * what turns the scoring model from a black box into something an engineer
 * can interrogate - and it is how the weights get validated against wells
 * whose relationships are already known.
 */
export default function WeightSliders({ weights, onChange, onReset }) {
  const total = Object.values(weights).reduce((a, b) => a + b, 0) || 1

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between">
        <p className="flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wide text-slate-500">
          <SlidersHorizontal className="h-3.5 w-3.5" /> Relevance weights
        </p>
        <button type="button" className="btn !px-2 !py-1 text-xs" onClick={onReset}>
          <RotateCcw className="h-3 w-3" /> Reset
        </button>
      </div>

      {Object.entries(LABELS).map(([key, label]) => (
        <div key={key}>
          <div className="flex items-baseline justify-between text-xs">
            <label htmlFor={`w-${key}`} className="text-slate-600">
              {label}
            </label>
            <span className="font-mono text-slate-500">
              {((weights[key] / total) * 100).toFixed(0)}%
            </span>
          </div>
          <input
            id={`w-${key}`}
            type="range"
            min="0"
            max="0.5"
            step="0.01"
            value={weights[key]}
            onChange={(e) => onChange({ ...weights, [key]: Number(e.target.value) })}
            className="mt-1 w-full accent-oil-600"
          />
        </div>
      ))}

      <p className="text-[11px] leading-snug text-slate-500">
        Weights are normalised, so they do not need to add up to one. The ranking updates as you
        move them.
      </p>
    </div>
  )
}
