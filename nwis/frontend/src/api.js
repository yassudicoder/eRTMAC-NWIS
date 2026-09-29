/**
 * Thin client over the NWIS API.
 *
 * Every call goes through `request`, so loading and error handling behave the
 * same everywhere and a failed fetch surfaces the server's own message rather
 * than a bare "Failed to fetch".
 */

const BASE = import.meta.env.VITE_API_BASE ?? ''

async function request(path, params) {
  const url = new URL(`${BASE}${path}`, window.location.origin)
  if (params) {
    for (const [key, value] of Object.entries(params)) {
      if (value !== undefined && value !== null && value !== '') {
        url.searchParams.set(key, value)
      }
    }
  }
  const response = await fetch(url)
  if (!response.ok) {
    let detail = `${response.status} ${response.statusText}`
    try {
      const body = await response.json()
      if (body?.detail) detail = typeof body.detail === 'string' ? body.detail : JSON.stringify(body.detail)
    } catch {
      /* response had no JSON body */
    }
    throw new Error(detail)
  }
  return response.json()
}

/** Turn the weight slider state into `w_*` query parameters. */
export function weightParams(weights) {
  if (!weights) return {}
  return Object.fromEntries(Object.entries(weights).map(([k, v]) => [`w_${k}`, v]))
}

export const api = {
  health: () => request('/api/health'),
  stats: () => request('/api/stats'),
  fields: () => request('/api/fields'),
  stratigraphy: () => request('/api/stratigraphy'),

  wells: (params) => request('/api/wells', params),
  well: (id) => request(`/api/wells/${id}`),
  wellLog: (id, params) => request(`/api/wells/${id}/log`, params),
  wellEvents: (id, params) => request(`/api/wells/${id}/events`, params),

  offsets: (id, { radius_km, limit, weights, focus_from_tvd, focus_to_tvd } = {}) =>
    request(`/api/wells/${id}/offsets`, {
      radius_km, limit, focus_from_tvd, focus_to_tvd, ...weightParams(weights),
    }),
  correlation: (id, offsetId, params) =>
    request(`/api/wells/${id}/correlation/${offsetId}`, params),
  risk: (id, { bit_md, lookahead_m, radius_km, weights } = {}) =>
    request(`/api/wells/${id}/risk`, {
      bit_md, lookahead_m, radius_km, ...weightParams(weights),
    }),
  signals: (id, params) => request(`/api/wells/${id}/signals`, params),

  documents: (params) => request('/api/documents', params),
  documentPage: (documentId, page, highlight) =>
    request(`/api/documents/${documentId}/page/${page}`, { highlight }),
  event: (eventId) => request(`/api/events/${eventId}`),

  npt: () => request('/api/analytics/npt'),
  fieldSummary: () => request('/api/analytics/field-summary'),
  clusters: (params) => request('/api/analytics/clusters', params),

  /** URL for the Server-Sent Events replay stream. */
  streamUrl: (id, { md_from, interval_ms, lookahead_m, radius_km } = {}) => {
    const url = new URL(`${BASE}/api/realtime/${id}/stream`, window.location.origin)
    for (const [key, value] of Object.entries({ md_from, interval_ms, lookahead_m, radius_km })) {
      if (value !== undefined && value !== null) url.searchParams.set(key, value)
    }
    return url.toString()
  },
}
