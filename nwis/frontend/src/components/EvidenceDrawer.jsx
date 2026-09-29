import { useEffect, useRef, useState } from 'react'
import { FileText, X, ChevronLeft, ChevronRight, ScanLine } from 'lucide-react'
import { api } from '../api'
import { fmt } from '../format'
import { ErrorBox, Loading } from './ui'

/**
 * Opens the actual page of the actual report an alert is based on, with the
 * cited line marked.
 *
 * This is the answer to "why should I believe this?", and it is the reason
 * the ingestion pipeline carries a document id, page and line number all the
 * way through to the alert.
 */
export default function EvidenceDrawer({ citation, onClose }) {
  const [page, setPage] = useState(citation?.page ?? 1)
  const [doc, setDoc] = useState(null)
  const [error, setError] = useState(null)
  const [loading, setLoading] = useState(true)
  const highlightRef = useRef(null)

  useEffect(() => {
    setPage(citation?.page ?? 1)
  }, [citation?.document_id, citation?.page])

  useEffect(() => {
    if (!citation) return
    let live = true
    setLoading(true)
    setError(null)
    api
      .documentPage(citation.document_id, page, page === citation.page ? citation.line_no : undefined)
      .then((d) => live && setDoc(d))
      .catch((e) => live && setError(e.message))
      .finally(() => live && setLoading(false))
    return () => {
      live = false
    }
  }, [citation, page])

  // Land the reader on the cited line rather than the top of the page.
  useEffect(() => {
    if (!doc || loading) return
    highlightRef.current?.scrollIntoView({ block: 'center', behavior: 'smooth' })
  }, [doc, loading])

  useEffect(() => {
    const onKey = (e) => e.key === 'Escape' && onClose()
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  if (!citation) return null

  return (
    <div className="fixed inset-0 z-[1000] flex justify-end">
      <div
        className="absolute inset-0 bg-slate-900/40 backdrop-blur-[1px]"
        onClick={onClose}
        role="presentation"
      />
      <aside className="relative flex h-full w-full max-w-2xl flex-col bg-white shadow-2xl">
        <header className="flex items-start justify-between gap-3 border-b border-slate-200 px-5 py-4">
          <div className="min-w-0">
            <p className="flex items-center gap-2 text-sm font-semibold text-slate-900">
              <FileText className="h-4 w-4 text-oil-700" />
              {doc?.title ?? citation.document_id}
            </p>
            <p className="mt-0.5 text-xs text-slate-500">
              {citation.document_id} &middot; {citation.doc_type} &middot; page {page}
              {doc?.page_count ? ` of ${doc.page_count}` : ''} &middot; line {citation.line_no}
            </p>
            {doc?.extraction_method && (
              <p className="mt-1 inline-flex items-center gap-1 text-[11px] text-slate-500">
                <ScanLine className="h-3 w-3" />
                Ingested as {doc.extraction_method}
                {doc.extraction_method === 'ocr' && doc.ocr_confidence != null
                  ? ` (OCR confidence ${fmt.pct(doc.ocr_confidence)})`
                  : ''}
              </p>
            )}
          </div>
          <button type="button" className="btn" onClick={onClose} aria-label="Close evidence">
            <X className="h-4 w-4" />
          </button>
        </header>

        <div className="flex items-center justify-between gap-2 border-b border-slate-200 bg-slate-50 px-5 py-2">
          <button
            type="button"
            className="btn"
            disabled={page <= 1}
            onClick={() => setPage((p) => Math.max(1, p - 1))}
          >
            <ChevronLeft className="h-4 w-4" /> Previous
          </button>
          <span className="text-xs text-slate-500">
            Page {page}
            {doc?.page_count ? ` / ${doc.page_count}` : ''}
          </span>
          <button
            type="button"
            className="btn"
            disabled={doc?.page_count ? page >= doc.page_count : false}
            onClick={() => setPage((p) => p + 1)}
          >
            Next <ChevronRight className="h-4 w-4" />
          </button>
        </div>

        <div className="flex-1 overflow-auto bg-slate-50 p-5">
          {loading && <Loading label="Opening the report" />}
          {error && <ErrorBox message={error} />}
          {doc && !loading && (
            <div className="rounded-lg border border-slate-200 bg-white p-4 shadow-sm">
              {doc.lines.map((line) => (
                <div
                  key={line.line_no}
                  ref={line.highlighted ? highlightRef : undefined}
                  className={`evidence-line flex gap-3 rounded px-2 py-0.5 ${
                    line.highlighted ? 'bg-amber-100 ring-1 ring-amber-300' : ''
                  }`}
                >
                  <span className="w-10 shrink-0 select-none text-right text-slate-300">
                    {line.line_no}
                  </span>
                  <span className={line.highlighted ? 'font-semibold text-slate-900' : 'text-slate-700'}>
                    {line.text || ' '}
                  </span>
                </div>
              ))}
            </div>
          )}
        </div>

        <footer className="border-t border-slate-200 bg-white px-5 py-3">
          <p className="text-xs font-semibold uppercase tracking-wide text-slate-500">
            Extracted from this line
          </p>
          <p className="mt-1 rounded border border-amber-200 bg-amber-50 p-2 font-mono text-xs text-slate-800">
            {citation.snippet}
          </p>
        </footer>
      </aside>
    </div>
  )
}
