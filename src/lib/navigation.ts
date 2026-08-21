/**
 * The tool's modules — one source of truth for the sidebar, the mobile drawer,
 * and the dashboard's feature explorer (SPEC-005 §7).
 *
 * `status` is honest on purpose. A nav that links to nine features and silently
 * 404s on five is worse than one that says which five are specified but not yet
 * built: `planned` entries route to a module page explaining the feature and
 * naming its spec, rather than pretending to work or vanishing from the menu.
 */

export type ModuleStatus = 'live' | 'planned'

export interface NavModule {
  slug: string
  label: string
  /** Where it goes when live. `planned` modules use `/dashboard/modules/<slug>`. */
  href: string
  icon: string
  status: ModuleStatus
  /** One line for the nav tooltip and the explorer card. */
  blurb: string
  /** What it does, for the module page. */
  detail: string
  /** Spec traceability, shown on the module page. */
  spec: string
  /** Capabilities listed on the module page. */
  highlights: string[]
}

export interface NavGroup {
  id: string
  label: string
  icon: string
  modules: NavModule[]
}

export const NAV_GROUPS: readonly NavGroup[] = [
  {
    id: 'overview',
    label: 'Voice & overview',
    icon: 'bi-grid-1x2',
    modules: [
      {
        slug: 'vowcraft',
        label: 'Voice to BRD',
        href: '/dashboard',
        icon: 'bi-mic-fill',
        status: 'live',
        blurb: 'Speak a requirement; get a business requirements document.',
        detail:
          'The tool\u2019s primary act, and the landing screen. One record button: speak naturally and ' +
          'the transcription appears as you talk, then a structured BRD is written from it. Speaking ' +
          'again amends that document in place rather than regenerating it, so requirement ids stay ' +
          'stable and nothing you already accepted is lost.',
        spec: 'SPEC-014',
        highlights: [
          'Live transcription while you speak, via streaming ASR',
          'Structured document: scope, stakeholders, FR/NFR, risks, assumptions',
          'Incremental refinement \u2014 speak again to amend, not regenerate',
          'Open questions instead of invented specifics',
          'Export as Markdown or JSON',
        ],
      },
      {
        slug: 'brd-history',
        label: 'Requirements documents',
        href: '/dashboard/brd',
        icon: 'bi-file-earmark-text',
        status: 'live',
        blurb: 'Every document you have dictated, and what each spoken turn changed.',
        detail:
          'The history for the voice surface. Each document keeps every revision, pairing what you ' +
          'said with what it changed \u2014 which is the only way an incremental edit is auditable ' +
          'without diffing two versions by eye.',
        spec: 'SPEC-014 \u00a76',
        highlights: [
          'Per-revision record of what was said and what changed',
          'Requirement counts and open-question counts at a glance',
          'Resume a recording that was interrupted',
          'Markdown and JSON export',
        ],
      },
      {
        slug: 'dashboard',
        label: 'Analytics overview',
        href: '/dashboard/overview',
        icon: 'bi-speedometer2',
        status: 'live',
        blurb: 'Usage, workflow funnel, and guardrail activity at a glance.',
        detail:
          'Answers four questions in order: where work stands, whether the workflow is moving, ' +
          'what the guardrails did, and what state the system is in.',
        spec: 'SPEC-005 §7',
        highlights: [
          'Pending decisions as the headline number',
          'Extracted → decided → approved → executed funnel with drop-off',
          'Guardrail blocks and warnings by rule id',
          'Module availability and integration mode',
        ],
      },
    ],
  },
  {
    id: 'capture',
    label: 'Capture & transcribe',
    icon: 'bi-soundwave',
    modules: [
      {
        slug: 'upload',
        label: 'Recordings',
        href: '/dashboard/transcripts',
        icon: 'bi-cloud-arrow-up',
        status: 'live',
        blurb: 'Upload a meeting; get a transcript and action items from it.',
        detail:
          'Audio or video, up to 25 MB, type-checked by its magic bytes rather than its extension. ' +
          'Transcription runs after the response with progress on the row, so a reload never loses ' +
          'it, and the two stages fail independently — a transcript whose extraction failed keeps ' +
          'the transcript and can be re-extracted alone.',
        spec: 'SPEC-010',
        highlights: [
          'MP3, M4A, WAV, WebM, OGG, FLAC, MP4, MOV',
          'Free-tier transcription via Groq Whisper; no card needed',
          'Word-level timings persisted for the player',
          'A bundled sample so the pipeline runs with no API key at all',
          'Identical re-uploads reuse the transcript instead of spending quota',
        ],
      },
      {
        slug: 'transcripts',
        label: 'Transcript reader',
        href: '/dashboard/transcripts/reader',
        icon: 'bi-file-earmark-text',
        status: 'live',
        blurb: 'Read and hear a recording, with the spoken word highlighted.',
        detail:
          'Plays the recording with the current word highlighted, and every word is a button that ' +
          'seeks to it. Its real purpose is closing the grounding loop: an action item cites a ' +
          'timestamp and a quote, and clicking that citation opens the transcript on the sentence ' +
          'so you can hear it rather than take it on trust.',
        spec: 'SPEC-012',
        highlights: [
          'Word-level highlighting during playback',
          'Click any word to jump there; deep links from an action item’s citation',
          'Follow mode that yields to manual scrolling',
          'Rename Speaker 1 to a real name, applied everywhere',
          'Find in transcript, and export as TXT, Markdown, SRT or WebVTT',
          'Speaker diarization still needs the Python service (SPEC-011)',
        ],
      },
      {
        slug: 'live-meetings',
        label: 'Live meetings',
        href: '/dashboard/transcripts/live',
        icon: 'bi-camera-video',
        status: 'live',
        blurb: 'Share a browser tab with meeting audio and get a transcript as it ends.',
        detail:
          'Works with any meeting tool running in a browser tab — Meet, Teams, Zoom or otherwise ' +
          '— because it captures the tab’s own audio via the browser’s share-tab control rather ' +
          'than joining the call as a bot (Google’s own bot API is gated behind a developer-preview ' +
          'program requiring every participant enrolled, with no timeline to open up). Streams over ' +
          'the same relay live dictation already uses, and lands as an ordinary recording once you stop.',
        spec: 'SPEC-013',
        highlights: [
          'Vendor-agnostic: any meeting tool open in a shared browser tab, not just Meet or Teams',
          'Streaming transcription over the existing live-transcription relay',
          'Manual start (share the tab) and stop (native "Stop sharing", or a button here)',
          'Lands as a normal recording — segments, and action items once extraction runs',
        ],
      },
    ],
  },
  {
    id: 'understand',
    label: 'Understand',
    icon: 'bi-diagram-3',
    modules: [
      {
        slug: 'action-items',
        label: 'Action items',
        href: '/dashboard/action-items',
        icon: 'bi-list-check',
        status: 'live',
        blurb: 'Review, approve, and execute what the conversation committed to.',
        detail:
          'The core of the tool. Every extracted action carries an owner, a deadline, a priority, ' +
          'a confidence score, and the timestamp and quote it came from — so a reviewer can decide ' +
          'without replaying the meeting.',
        spec: 'SPEC-001',
        highlights: [
          'Three groups: ready, needs clarification, informational',
          'Nine composable filters, all reflected in the URL',
          'Approve, edit, reject, defer — or execute with confirmation',
          'Superseded actions and dependency chains surfaced',
          'Keyboard driven: ⌘K, then a / r / d / e',
        ],
      },
      {
        slug: 'insights',
        label: 'Decisions & summary',
        href: '/dashboard/insights',
        icon: 'bi-lightbulb',
        status: 'live',
        blurb: 'Every decision a meeting settled, searchable across every recording.',
        detail:
          'The Decision table already exists and the POL_CONTRADICTS_DECISION guardrail already ' +
          'cites it — this module is the reading surface for that data.',
        spec: 'SPEC-020',
        highlights: [
          'Decisions with who made them and when',
          'Every decision links back to its moment in the transcript',
          'Searchable and filterable across every meeting',
          'Meeting summaries alongside the decisions they came from',
        ],
      },
      {
        slug: 'search',
        label: 'Semantic search',
        href: '/dashboard/search',
        icon: 'bi-search',
        status: 'live',
        blurb: 'Ask "what did they say about the budget?" and find it by meaning.',
        detail:
          'pgvector (HNSW, cosine) over segment embeddings, so a query matches sense rather ' +
          'than exact wording. Voyage AI is the embedding provider for now — its free tier ' +
          'needs no card; add a key in Settings → API keys.',
        spec: 'SPEC-021',
        highlights: [
          'Meaning-based retrieval across every transcript',
          'Jump straight to the moment in the audio',
          "Voyage AI's free tier needs no card",
        ],
      },
    ],
  },
  {
    id: 'execute',
    label: 'Execute & verify',
    icon: 'bi-lightning-charge',
    modules: [
      {
        slug: 'integrations',
        label: 'Integrations',
        href: '/dashboard/settings/integrations',
        icon: 'bi-plug',
        status: 'live',
        blurb: 'Where approved actions land: Calendar, Notion, Gmail, SendGrid, Slack.',
        detail:
          'Five adapters behind one interface. Mock mode exercises every path with no credentials, ' +
          'so the approval flow is testable before any account is connected.',
        spec: 'SPEC-002',
        highlights: [
          'Google Calendar, Notion, Gmail, SendGrid, Slack',
          'OAuth with PKCE, encrypted tokens, automatic refresh',
          'Mock mode by default — nothing leaves the machine',
          'Bounded retries with full jitter and idempotency',
        ],
      },
      {
        slug: 'audit-log',
        label: 'Audit log',
        href: '/dashboard/audit-log',
        icon: 'bi-journal-text',
        status: 'live',
        blurb: 'Every proposal, decision, and execution — append-only.',
        detail:
          'The immutable record of what the agent proposed, what a human decided, and what happened ' +
          'in the world. No update or delete path exists anywhere in the codebase.',
        spec: 'SPEC-003 §7',
        highlights: [
          'Append-only: no mutation endpoint exists',
          'Before and after snapshots on every change',
          'Rule ids recorded for blocked and warned executions',
          'Survives account deletion — the record outlives the actor',
        ],
      },
      {
        slug: 'workflows',
        label: 'Multi-step workflows',
        // Not its own page — a contextual action on the Action Items board (a "blocks N
        // others → run workflow" affordance on the blocking item's card). The query string
        // exists only so this href is distinct from the `action-items` module's own
        // (`/dashboard/action-items`) — see navigation.test.ts's "no two modules share an
        // href" check, which exists for exactly this reason.
        href: '/dashboard/action-items?workflows=1',
        icon: 'bi-diagram-2',
        status: 'live',
        blurb: 'An item that blocks others can run itself and everything downstream in one go.',
        detail:
          'Shipped as a contextual action on the Action Items board, not a page of its own: any ' +
          'card that blocks others (via `dependsOnId`) now offers "run workflow," which walks that ' +
          'chain forward and calls the same executor for each step in order. (`parentId`/`stepOrder` ' +
          'are separate, unused schema columns; nothing populates them, so they play no part in this.)',
        spec: 'SPEC-002 §8',
        highlights: [
          'Runs a chain of dependent items in one action, from any card that blocks others',
          'Halts the whole run on the first terminal failure — nothing after it is attempted',
          'Every step still re-checks its own approval and guardrails when it actually runs',
        ],
      },
    ],
  },
  {
    id: 'settings',
    label: 'Settings',
    icon: 'bi-sliders',
    modules: [
      {
        slug: 'preferences',
        label: 'Preferences',
        href: '/dashboard/settings',
        icon: 'bi-toggles',
        status: 'live',
        blurb: 'The guardrail envelope: hours, limits, thresholds, routing.',
        detail:
          'Every control here feeds the pure rule engine, so this screen is literally what the ' +
          'agent is allowed to do. Each field names the rule it governs.',
        spec: 'SPEC-005 §4.1',
        highlights: [
          'Working hours and weekend policy',
          'Meeting length and buffer limits',
          'Org domains — what counts as an internal recipient',
          'Approval thresholds per action type (tighten only)',
          'Which provider executes email: Gmail or SendGrid',
        ],
      },
      {
        slug: 'credentials',
        label: 'API keys',
        href: '/dashboard/settings/credentials',
        icon: 'bi-key',
        status: 'live',
        blurb: '31 providers, 14 of them free. Encrypted at rest.',
        detail:
          'Bring your own keys for transcription, extraction, translation, embeddings, and ' +
          'integrations. Free tiers sort first with their actual allowances.',
        spec: 'SPEC-004',
        highlights: [
          'AES-256-GCM at rest, bound to your account',
          'No read path for a stored secret — masked hints only',
          'One-click verification against each provider',
          'Falls back to the environment when you set nothing',
        ],
      },
      {
        slug: 'profile',
        label: 'Profile',
        href: '/dashboard/settings/profile',
        icon: 'bi-person-circle',
        status: 'live',
        blurb: 'Your name, your password, and account deletion.',
        detail:
          'Identity is kept separate from preferences: one is who you are, the other is what the ' +
          'agent may do on your behalf.',
        spec: 'SPEC-005 §3, §4, §8',
        highlights: [
          'Rename yourself; it shows in the audit trail',
          'Change password — the current one is required',
          'scrypt hashing, upgradable cost factor per row',
          'Delete the account with a 7-day grace period',
        ],
      },
    ],
  },
]

export const ALL_MODULES: readonly NavModule[] = NAV_GROUPS.flatMap((g) => g.modules)

export function findModule(slug: string): { module: NavModule; group: NavGroup } | null {
  for (const group of NAV_GROUPS) {
    const module = group.modules.find((m) => m.slug === slug)
    if (module) return { module, group }
  }
  return null
}

export function groupFor(slug: string): NavGroup | null {
  return findModule(slug)?.group ?? null
}

/**
 * Whether `module` should render as the sidebar's active entry for `pathname` (plus
 * `search`, the current `?query=string` with or without its leading `?`) — SPEC-005 §2.
 * Pure and dependency-free on purpose (moved out of `Sidebar.tsx`, a `'use client'`
 * component, specifically so this is unit-testable without a DOM): a shared URL between
 * modules has bitten this three times now — first a shared *prefix* (Recordings vs.
 * Transcript reader, then Live meetings falling into the reader's catch-all), then a
 * shared *pathname distinguished only by query string* (Multi-step workflows, which
 * lives at `/dashboard/action-items?workflows=1` since it has no page of its own —
 * `usePathname()` strips query strings entirely, so comparing pathname alone meant
 * Action Items lit up and Workflows never could, regardless of which link was actually
 * followed).
 */
export function isActive(module: NavModule, pathname: string, search: string = ''): boolean {
  const params = new URLSearchParams(search)
  if (module.href === '/dashboard') return pathname === '/dashboard'
  // `/dashboard/settings` would otherwise light up for every settings subpage.
  if (module.href === '/dashboard/settings') return pathname === '/dashboard/settings'
  // Recordings (the list), Transcript reader (a specific transcript, or its own landing
  // page), and Live meetings all share the `/dashboard/transcripts` prefix but are three
  // different destinations. Recordings claims only the bare list; Live meetings claims
  // its own subtree; everything else under the prefix belongs to the reader — which is
  // why, without carving both exceptions out explicitly, a third module added under this
  // prefix (Live meetings) fell into the reader's catch-all and both lit up together.
  if (module.slug === 'upload' && module.href === '/dashboard/transcripts') {
    return pathname === '/dashboard/transcripts'
  }
  if (module.slug === 'live-meetings') return pathname.startsWith('/dashboard/transcripts/live')
  if (module.slug === 'transcripts') {
    return pathname.startsWith('/dashboard/transcripts/') && !pathname.startsWith('/dashboard/transcripts/live')
  }
  // Multi-step workflows and Action items share a pathname; only the `workflows` query
  // param (present only when Workflows' own link was followed) tells them apart. Action
  // items must NOT also light up when it's present, or both light up together again.
  if (module.slug === 'workflows') return pathname === '/dashboard/action-items' && params.has('workflows')
  if (module.slug === 'action-items') {
    return pathname === '/dashboard/action-items' && !params.has('workflows')
  }
  return pathname === module.href || pathname.startsWith(`${module.href}/`)
}
