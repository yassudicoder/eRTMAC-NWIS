export const meta = {
  name: 'sih-ps121-compliance-audit',
  description: 'Audit the NWIS codebase against every clause of SIH PS 121 (Oil India, eRTMAC-NWIS)',
  phases: [
    { title: 'Audit', detail: 'one skeptical auditor per PS clause' },
    { title: 'Critique', detail: 'completeness critic over the whole PS' },
  ],
}

const ROOT = 'C:/Users/yashd/Downloads/SIH 2nd PS/nwis'

const PS_TEXT = `
SIH 2026 Problem Statement 121 (PS Number 26121) - Oil India Limited
Title: eRTMAC-NWIS (Nearby Wells Intelligence System): An AI-Powered Offset Well
Knowledge and Decision Support Platform for Drilling Operations
Category: Software   Theme: Smart Automation

BACKGROUND: Oil India Limited has a digital real-time monitoring system (eRTMAC)
providing real-time drilling data, mud logging information and wellsite analytics.
Drilling decisions in geologically complex formations require not only real-time data
from the active well but also insights from nearby and historical wells drilled in the
same reservoir or formation. Historical drilling knowledge resides across numerous well
completion reports, drilling reports, PDF documents and individual experience, making
retrieval time-consuming and dependent on individual experience and memory.

PROBLEM DESCRIPTION - drilling teams lack a unified platform that can:
  PD-i   Display nearby wells on a geospatial map relative to the active well.
  PD-ii  Provide instant access to historical drilling experiences and operational
         events from offset wells.
  PD-iii Correlate drilling parameters, reservoir characteristics, mud losses, kicks,
         stuck pipe incidents, casing programs, cementing practices, and
         formation-specific risks across wells.
  PD-iv  Generate proactive alerts when current drilling operations approach depths or
         formations where similar challenges were encountered in nearby wells.

EXPECTED OUTCOME - develop an AI/ML-enabled NWIS that acts as a standalone
decision-support platform alongside eRTMAC with institutional memory. It should:
  EO-i   Use AI, NLP, OCR and data analytics to automatically extract and structure
         information from historical drilling reports and well documents.
  EO-ii  Provide an interactive map-based visualization of nearby wells within a
         user-defined radius.
  EO-iii Create a searchable knowledge repository of drilling events, lessons learned,
         operational challenges, and mitigation measures.
  EO-iv  Correlate geological, drilling, and reservoir data across wells based on depth
         and formation.
  EO-v   Develop predictive analytics models that can identify potential drilling risks
         such as mud losses, stuck pipe, overpressure zones, torque spikes, or
         cementing issues based on historical offset-well behaviour.
`

const CLAUSES = [
  { id: 'PD-i', text: 'Display nearby wells on a geospatial map relative to the active well.' },
  { id: 'PD-ii', text: 'Provide instant access to historical drilling experiences and operational events from offset wells.' },
  { id: 'PD-iii', text: 'Correlate drilling parameters, reservoir characteristics, mud losses, kicks, stuck pipe incidents, CASING PROGRAMS, CEMENTING PRACTICES, and formation-specific risks across wells. Check EACH named item separately - especially casing programs, cementing practices and reservoir characteristics, which are easy to miss.' },
  { id: 'PD-iv', text: 'Generate proactive alerts when current drilling operations approach depths or formations where similar challenges were encountered in nearby wells.' },
  { id: 'EO-i', text: 'Use AI, NLP, OCR and data analytics to automatically extract and structure information from historical drilling reports and well documents. Assess whether the OCR path actually works or is only stubbed, and whether "AI" is a fair description of what is implemented.' },
  { id: 'EO-ii', text: 'Provide an interactive map-based visualization of nearby wells within a USER-DEFINED RADIUS. Verify the radius is genuinely user-controllable in the UI.' },
  { id: 'EO-iii', text: 'Create a SEARCHABLE knowledge repository of drilling events, LESSONS LEARNED, operational challenges, and MITIGATION MEASURES. Check each of the four separately: is there real search (full-text across the corpus, not just filtering a list)? Are lessons learned captured as first-class data? Are mitigation measures captured?' },
  { id: 'EO-iv', text: 'Correlate geological, drilling, and RESERVOIR data across wells based on depth and formation. Check whether reservoir data (porosity, permeability, pressure, fluid, pay) exists at all.' },
  { id: 'EO-v', text: 'Develop PREDICTIVE ANALYTICS MODELS that can identify potential drilling risks such as mud losses, stuck pipe, OVERPRESSURE ZONES, torque spikes, or CEMENTING ISSUES based on historical offset-well behaviour. Check each named risk separately. Also judge honestly whether what exists is a trained/validated predictive MODEL or a hand-tuned heuristic score.' },
  { id: 'OVERALL', text: 'Acts as a standalone AI/ML-enabled decision-support platform alongside eRTMAC that carries institutional memory. Judge the system as a whole against the background and framing of the PS.' },
]

const AUDIT_SCHEMA = {
  type: 'object',
  properties: {
    clause_id: { type: 'string' },
    verdict: { type: 'string', enum: ['MET', 'PARTIAL', 'MISSING'] },
    confidence: { type: 'string', enum: ['high', 'medium', 'low'] },
    what_exists: { type: 'string', description: 'What the codebase actually does for this clause, with file paths' },
    evidence: {
      type: 'array',
      items: {
        type: 'object',
        properties: {
          file: { type: 'string' },
          detail: { type: 'string' },
        },
        required: ['file', 'detail'],
      },
    },
    gaps: {
      type: 'array',
      description: 'Specific, concrete things the PS asks for that are absent or weak. Empty if MET.',
      items: { type: 'string' },
    },
    recommendation: { type: 'string', description: 'What to build to close the gap, concretely' },
    severity: { type: 'string', enum: ['blocker', 'major', 'minor', 'none'], description: 'How badly this would hurt in SIH evaluation' },
  },
  required: ['clause_id', 'verdict', 'confidence', 'what_exists', 'evidence', 'gaps', 'recommendation', 'severity'],
}

phase('Audit')
log(`Auditing ${CLAUSES.length} PS clauses against the codebase`)

const audits = await parallel(CLAUSES.map((c) => () =>
  agent(
    `You are auditing a Smart India Hackathon submission against its official problem statement.
The project is at ${ROOT} — a prototype called NWIS for Oil India Limited.

Here is the FULL problem statement:
${PS_TEXT}

YOUR ASSIGNED CLAUSE IS **${c.id}**:
"${c.text}"

Audit ONLY this clause. Read the actual code — do not guess. Relevant places to look:
  ${ROOT}/backend/app/            assam_geology.py, store.py, ingest/, engine/, routers/
  ${ROOT}/frontend/src/           App.jsx, views/, components/
  ${ROOT}/scripts/                generate_assam_dataset.py and the verification scripts
  ${ROOT}/README.md, docs/
  ${ROOT}/data/synthetic/         the generated corpus (wells.json, documents/)

Be SKEPTICAL and adversarial. Your job is to find what is MISSING or WEAK, not to
confirm what is present. A hackathon judge will read the PS line by line and check each
named item. If the clause names specific items (e.g. "casing programs", "cementing
practices", "lessons learned", "overpressure zones"), check EACH ONE INDIVIDUALLY and
say which are absent — a clause is not MET if two of its five named items are missing.

Distinguish carefully between:
  - data that is GENERATED into a file but never INGESTED into the knowledge base
  - a feature that is described in the README but not actually implemented
  - a code path that exists but is optional/untested/stubbed
  - a heuristic presented as a "model"

Grep for the specific terms. Read the files. Cite file paths and line numbers in your
evidence. Set verdict MET only if a judge reading the PS would tick the box without
argument.`,
    { label: `audit:${c.id}`, phase: 'Audit', schema: AUDIT_SCHEMA },
  )))

const results = audits.filter(Boolean)
log(`${results.length}/${CLAUSES.length} audits returned`)

phase('Critique')

const CRITIC_SCHEMA = {
  type: 'object',
  properties: {
    missed_requirements: {
      type: 'array',
      description: 'Requirements in the PS text that none of the auditors covered',
      items: {
        type: 'object',
        properties: {
          requirement: { type: 'string' },
          why_it_matters: { type: 'string' },
          status_guess: { type: 'string', enum: ['likely met', 'likely partial', 'likely missing'] },
        },
        required: ['requirement', 'why_it_matters', 'status_guess'],
      },
    },
    disputed_verdicts: {
      type: 'array',
      description: 'Auditor verdicts you believe are too generous or too harsh, with reasoning',
      items: {
        type: 'object',
        properties: {
          clause_id: { type: 'string' },
          auditor_verdict: { type: 'string' },
          your_verdict: { type: 'string' },
          reasoning: { type: 'string' },
        },
        required: ['clause_id', 'auditor_verdict', 'your_verdict', 'reasoning'],
      },
    },
    priority_order: {
      type: 'array',
      description: 'The gaps to close, most important first, judged by SIH evaluation impact',
      items: { type: 'string' },
    },
  },
  required: ['missed_requirements', 'disputed_verdicts', 'priority_order'],
}

const critique = await agent(
  `You are the completeness critic on a Smart India Hackathon compliance audit.

Here is the FULL problem statement:
${PS_TEXT}

Here is what ${results.length} independent auditors reported after reading the codebase at ${ROOT}:

${JSON.stringify(results, null, 2)}

Your job:
1. Read the PS text again word by word. What requirements did NO auditor cover? Look
   especially at the BACKGROUND paragraph and the framing sentences, not just the
   numbered lists — e.g. "PDF documents", "mud logging information", "same reservoir or
   formation", "standalone platform alongside eRTMAC", "institutional memory",
   "Category: Software", "Theme: Smart Automation".
2. Challenge verdicts you think are wrong in EITHER direction. Be specific. Read the
   code yourself to settle disputes — do not just trust the auditors.
3. Rank every confirmed gap by how much it would cost the team in SIH evaluation.
   A judge ticking named items off a list will notice an absent named item immediately;
   an architectural nicety they will not.

Be rigorous. Read files where you need to.`,
  { label: 'completeness-critic', phase: 'Critique', schema: CRITIC_SCHEMA, effort: 'high' },
)

return { audits: results, critique }
