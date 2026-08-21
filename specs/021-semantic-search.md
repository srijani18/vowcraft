# SPEC-021 — Semantic search

**Status:** Accepted · **Implements:** Phase 2.5
**Depends on:** SPEC-010 §6 (segments, the unit this embeds), SPEC-004 (the credential vault Voyage's key lives in)

## 1. Purpose

A meeting is searched by what was meant, not what was typed: "what did they say about
the budget?" should find "we're pushing the Q3 spend review to next week" even though
neither shares a word with the other. `Segment` rows (SPEC-010 §6) already exist,
word-timed and chunked at a reasonable granularity — this spec embeds them and ranks a
query against them by vector similarity.

## 2. Scope

**In:** `Segment.text` only, embedded via Voyage AI (`voyage-3-lite`, 512 dimensions),
stored in a new `SegmentEmbedding` table (pgvector, HNSW index, cosine distance), queried
through `GET /api/search` and reindexed on demand via `POST /api/search/reindex`. A
minimal but real search page at `/dashboard/search`.

**Out, deliberately:**
- **BRD documents.** `BrdDocument.content` is a tool-schema JSONB shape, not prose —
  embedding it well needs a flattening/rendering step first, which is its own piece of
  work, not an extension of this one.
- **Action items.** `ActionItem.description` is already fully text-searchable via the
  existing dashboard's filters (SPEC-001), and every action item already cites its
  `sourceQuote`/`sourceTimestampMs` back into a `Segment` — so a segment-level hit already
  surfaces the moment an action item came from, transitively.
- **Additional embedding providers** (Jina, local BGE). One provider, Voyage, for v1;
  `PROVIDERS` in `embeddings.py` is a tuple specifically so adding a second is additive,
  not a rewrite.
- **Re-chunking segments.** `Segment` is already provider-chunked at a sentence/utterance
  granularity; merging or splitting them for retrieval quality is a tuning pass, not a
  blocker for v1.

## 3. Why pgvector, HNSW/cosine, Voyage `voyage-3-lite`

**pgvector**, not a separate vector database: one fewer moving part, one fewer network
hop, and the existing Postgres instance already holds everything a hit needs to be
useful (transcript title, speaker, timestamp) — a join, not a second round trip.

**HNSW over IVFFlat**: IVFFlat needs a representative sample of *existing* data to pick
good list counts and is only accurate after that training pass — wrong for a table that
starts empty and grows from zero. HNSW builds incrementally and needs no training step.

**Cosine distance**, because Voyage explicitly recommends it for retrieval, and the
HNSW index is built with `vector_cosine_ops` to match — the index and the query operator
have to agree, or the index cannot be used at all.

**`voyage-3-lite` / 512 dimensions**: the model already named in this app's own credential
catalog (`apps/api/app/services/credentials.py`'s `voyage` `ServiceSpec`, `models=
("voyage-3", "voyage-3-lite")`), and its free tier is the reason Voyage was chosen over
requiring a paid provider outright. The column width is not a formality: changing models
later means a new migration and a full re-embed, since dimensionality is fixed per column.

## 4. The Prisma/Alembic dual-write hazard, and why it matters here specifically

This database has two schema owners: `web`'s `prisma db push --accept-data-loss` (every
boot, `docker/entrypoint.sh`) and `api`'s Alembic migrations. SPEC-015 §3.1 already
documents the consequence for `alembic_version` — Prisma has no notion of it and the push
leaves it looking dropped. A table Alembic creates that Prisma's schema does not know
about has the *same* problem, but worse: `db push --accept-data-loss` does not merely
forget about an unrecognized table, it drops it, silently, the next time `web` restarts
after `api` adds one.

`SegmentEmbedding` is therefore declared **twice**: for real, by Alembic
(`packages/db/alembic/versions/20260821_1814_add_segment_embeddings.py`, `CREATE
EXTENSION`, the table, the HNSW index); and as a `prisma/schema.prisma` shadow model using
`Unsupported("vector(512)")` for the embedding column, purely so `db push` sees the table
as already satisfied and leaves it alone. Prisma Client never reads or writes a row of it.

## 5. Contract

**Generation** — best-effort, in the ingest pipeline (`apps/api/app/services/ingest/
pipeline.py`), immediately after extraction, matching extraction's own "degrade, don't
fail" shape exactly: a transcript with no Voyage key configured still finishes `READY`.
`embed_segments_into()` (`apps/api/app/services/embeddings.py`) resolves the key through
the same BYOK vault every other provider uses (`CredentialService.resolve`, **not**
`resolve_module("EMBEDDING")`, which always reports available because of the unimplemented
`local_bge` catalog entry — the same trap the credentials migration found and routed
around; this spec routes around it the same way, with its own explicit `PROVIDERS` tuple).
Re-embedding a transcript deletes and reinserts its `SegmentEmbedding` rows rather than
updating in place — there is no partial-embedding state worth representing.

**`GET /api/search?q=&limit=&transcriptId=`** — embeds the query with `input_type:
"query"` (Voyage's asymmetric retrieval tuning expects the query and the indexed
passages embedded differently; using the wrong `input_type` silently degrades ranking
without ever erroring, so this distinction is load-bearing, not cosmetic), ranks by
cosine distance, returns each hit with enough context to be readable on its own —
transcript title, speaker, timestamp, the matched text, and its immediately adjacent
segment's text on each side (`null` at a transcript boundary) — since a bare sentence
fragment is a bad search result. No cursor pagination: this is ranked top-k retrieval,
not a browsable list, so a "page 2" has no stable meaning.

**Unconfigured key** — `resolve_embedder()` is called before anything else, so an
unconfigured user gets `503 embedding_unavailable` with a message pointing at Settings →
API keys, not a confusing empty result set or a generic 500.

**`POST /api/search/reindex`** — fire-and-forget (`202`-shaped `{"status": "started",
"transcripts": N}`, same pattern as `ingest.pipeline.start_processing`), embeds every
transcript that has at least one `Segment` with no `SegmentEmbedding` row yet. Chosen over
two alternatives: lazy-embed-on-search is circular for vector search specifically — you
cannot rank content that has not been embedded at query time — and auto-triggering a full
backfill the instant a key is saved conflates two very different operations (saving a
credential; running a potentially slow multi-transcript job) behind one click a user did
not expect to be slow.

## 6. Frontend

`/dashboard/search` (`SearchView.tsx`): a submit-on-Enter search box — not live-as-you-
type, since every keystroke would otherwise cost an embedding-API call against a token
budget that is not the app's own. Each hit links to `/dashboard/transcripts/{id}?t=
{startMs}`, the same deep-link convention `InsightsView.tsx` established for decisions.
An "add a Voyage key" banner appears when `GET /api/credentials`'s `voyage` entry has
`configured: false` — checked directly, not via the EMBEDDING module's aggregate status,
for the same `local_bge`-always-reports-available reason noted in §5. A "Reindex older
recordings" action surfaces `POST /api/search/reindex` inline, since that is where a user
will actually notice the gap (recordings from before a key was added), not buried in
Settings.

## 7. Testing

Following this repo's actual convention: service-layer integration tests against real
Postgres (no route-level test exists anywhere in this suite; `search.py`'s route stays an
untested thin pass-through like every other route file). HTTP to Voyage is mocked
(`test_embeddings_service.py`, patching `httpx.AsyncClient` the same way
`test_credentials_service.py` already mocks its own `verify()` HTTP calls) — storage and
ranking (`test_search_service.py`) are tested with pre-computed vectors and no HTTP
involved at all, so a ranking bug and an HTTP-integration bug can never be confused for
each other.

## 8. Follow-ups (explicitly out of scope here)

Additional embedding providers (Jina, local BGE); embedding BRDs and/or action items;
a `VerifyRecipe` for the `voyage` catalog entry (it currently has none, so the credentials
UI shows no "Test connection" for it); the catalog's "Free tier: 200M tokens" cost note,
which may already be stale relative to Voyage's real current offering — a catalog-accuracy
fix, not a semantic-search one.
