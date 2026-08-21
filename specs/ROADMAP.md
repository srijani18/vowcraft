# Roadmap — feature → spec → status

Traceability from the product brief to specs and code. **This repository
implements Phase 3 (the dashboard and execution path) plus the schema and
contracts the earlier phases will write into.**

## Phase 1 — Transcription
| # | Feature | Spec | Status |
|---|---|---|---|
| 1.1 | Upload audio/video (MP3, M4A, WAV, WebM, OGG, FLAC, MP4, MOV) | SPEC-010 §4 | **implemented** |
| 1.2 | Audio from video containers | SPEC-010 §4 | **implemented** (provider-side; no FFmpeg) |
| 1.3 | Multilingual transcription, auto-detect | SPEC-010 §6 | **implemented** (Groq/OpenAI Whisper) |
| 1.4 | Speaker diarization + name mapping | SPEC-011 | planned — Whisper does not diarize; reported honestly |
| 1.5 | Word-level timestamps | SPEC-010 §6 | **implemented** |
| 1.6 | Progress indicator | SPEC-010 §5 | **implemented** (stage + % on the row, polled) |
| 1.7 | Play audio, highlight current word | SPEC-012 | range-request streaming built; player surface planned |
| 1.8 | Live meeting capture, via browser tab audio (vendor-agnostic) | SPEC-013 | **implemented** — Google's Meet bot API is developer-preview-gated with no timeline; tab capture is the buildable substitute |
| 1.9 | Streaming ASR, provider-agnostic (Deepgram + self-hosted) | SPEC-014 §3 | **implemented** |
| 1.10 | Voice → BRD: live dictation to a structured document | SPEC-014 §5 | **implemented** |
| 1.11 | Incremental refinement — speak again to amend, ids stable | SPEC-014 §6 | **implemented** |
| 1.12 | BRD history with per-revision change summaries | SPEC-014 §8 | **implemented** |

## Phase 2 — Extraction
| # | Feature | Spec | Status |
|---|---|---|---|
| 2.1 | Structured action items | SPEC-010 §7 | **implemented** (tool use, quotes verified) |
| 2.2 | Superseded action detection | SPEC-010 §7 | **implemented** |
| 2.3 | Decisions extracted | SPEC-010 §7 | **implemented** |
| 2.4 | Meeting summary | SPEC-010 §7 | **implemented** |
| 2.6 | Free-tier provider registry (Groq/Gemini/Cerebras) | SPEC-010 §3 | **implemented** |
| 2.7 | Sample mode — full pipeline with no API key | SPEC-010 §3 | **implemented** |
| 2.5 | Semantic search (pgvector) | SPEC-021 | **implemented** (Voyage AI only; HNSW/cosine over Segment text) |

## Phase 3 — Execution ← **this repo**
| # | Feature | Spec | Status |
|---|---|---|---|
| 3.1 | Action items dashboard, filters, groups | SPEC-001 | **implemented** |
| 3.2 | Approve / edit / reject / defer | SPEC-001 §5 | **implemented** |
| 3.3 | Calendar / Task / Email / Reminder execution | SPEC-002 §3 | **implemented** |
| 3.4 | Confirmation before executing | SPEC-001 §11 | **implemented** |
| 3.5 | Execution status + `executionResult` | SPEC-002 §7 | **implemented** |
| 3.6 | OAuth, token refresh, retries | SPEC-002 §4, §6 | **implemented** |
| 3.7 | Risk tiers, rule engine, escalation | SPEC-003 | **implemented** |
| 3.8 | Audit log | SPEC-003 §7 | **implemented** |
| 3.9 | Bulk approve / reject | SPEC-001 §9 | **implemented** |
| 3.10 | Corrections captured | SPEC-003 §8 | **implemented** (capture only) |
| 3.11 | Dependencies, multi-step | SPEC-002 §8 | **implemented** (`dependsOnId` chain, sequential, halts entirely on first failure — `parentId`/`stepOrder` remain unused) |
| 3.12 | Decisions & summary reading surface | SPEC-020 | **implemented** |

## Cross-cutting — shipped alongside Phase 3
| # | Feature | Spec | Status |
|---|---|---|---|
| X.1 | Credential vault, 31 providers, encrypted at rest | SPEC-004 | **implemented** |
| X.2 | Landing overview: usage, funnel, guardrail activity | SPEC-005 §7 | **implemented** |
| X.3 | Preferences screen = the guardrail envelope | SPEC-005 §4.1 | **implemented** |
| X.4 | Profile: rename, password rotation, deletion | SPEC-005 §3, §8 | **implemented** |
| X.5 | First-run walkthrough with skip, persisted | SPEC-005 §6 | **implemented** |
| X.6 | Module nav, collapsible, mobile drawer | SPEC-005 §2 | **implemented** |
| X.7 | Audit-log viewer, filterable, read-only | SPEC-003 §7 | **implemented** |
| X.8 | Credential auth + signed sessions | SPEC-006 §2–4 | **implemented** |
| X.9 | Palette, contrast, theming, glow | SPEC-006 §5–7 | **implemented** |
| X.10 | Provider routing: Gmail *and* SendGrid for email | SPEC-005 §5 | **implemented** |
| X.11 | Marketing site (separate repo) | — | **implemented** |
| X.13 | Per-device session revocation | SPEC-006 | planned |
| X.14 | Hash-chained audit log | SPEC-003 §7 | planned |
| X.15 | SSO / SCIM | SPEC-006 | planned |
| X.16 | Password reset by email | SPEC-007 §2 | **implemented** |
| X.17 | Sign in with Google (OIDC + PKCE) | SPEC-007 §3 | **implemented** |
| X.18 | Welcome email on registration | SPEC-007 §4 | **implemented** |
| X.19 | Link back to the marketing site without signing out | — | **removed** — built in the sidebar footer, then removed as poor UX. The capability is unopposed; it just has no home in the app chrome. `NEXT_PUBLIC_MARKETING_URL` is still wired if it is wanted somewhere better. |

## Phase 4 — Advanced
| # | Feature | Spec | Status |
|---|---|---|---|
| 4.1 | Export PDF / DOCX / SRT / TXT | SPEC-030 | planned |
| 4.2 | Batch processing | SPEC-031 | planned |
| 4.3 | Custom vocabulary | SPEC-032 | planned |
| 4.4 | Analytics dashboard | SPEC-033 | planned |
| 4.5 | Translation layer | SPEC-034 | planned |
| 4.6 | Voice commands | SPEC-035 | planned |
| 4.7 | Offline / privacy-first (local models) | SPEC-036 | topology ready (SPEC-000 §2) |
| 4.8 | Correction-driven prompt learning | SPEC-003 §8 | data captured |
