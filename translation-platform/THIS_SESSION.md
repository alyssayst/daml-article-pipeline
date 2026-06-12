# Session Summary — Translation Platform

## Bug Fixes & Race Conditions

### SWR Auth Race Condition (Job Detail Page)
- **Problem:** Opening `/jobs/[id]` briefly flashed "Failed to load job: Not authenticated" for ~3 seconds before loading correctly
- **Root cause:** SWR fired its initial fetch before the Supabase auth token was restored from the async `getSession()` call
- **Fix:** Gated the SWR key on `authLoading` — passed `null` as the key while auth is loading, which tells SWR to skip fetching. Applied same `null`-key pattern already working on the dashboard.
- **File:** `frontend/src/app/jobs/[id]/page.tsx`

### Optimistic Updates — Translate/Claim State Flash
- **Problem:** Clicking Translate briefly showed the CLAIMED banner again ("Article extracted. Claim this job to start translating") before transitioning to IN_PROGRESS
- **Root cause:** `mutate()` with `revalidate: true` triggered an immediate SWR refetch. The refetch returned stale CLAIMED data because the background task hadn't committed IN_PROGRESS yet, overwriting the optimistic state
- **Fix:** Changed all action handlers (claim, unclaim, translate, retry) to use `revalidate: false` on their optimistic updates so the optimistic state sticks until SWR's normal polling picks up the real status
- **File:** `frontend/src/app/jobs/[id]/page.tsx`

### Translation Body Not Auto-Populating
- **Problem:** After translation completed, the title auto-populated but the body stayed empty
- **Root cause 1:** `TranslationEditor` used `useState` for title/body which only initializes from props on mount — when SWR polled and got the translation, the `detail` prop updated but local state didn't sync
- **Fix 1:** Added `useEffect` that syncs `title` and `body` state when `draft.title`/`draft.body` change
- **Root cause 2:** Tiptap's `useEditor` also only uses `content` as initial value, ignoring prop changes
- **Fix 2:** Added `useEffect` that calls `editor.commands.setContent()` when the `content` prop changes externally
- **Files:** `frontend/src/components/TranslationEditor.tsx`, `frontend/src/components/RichTextEditor.tsx`

### React Rules of Hooks Violation (Job Detail Page)
- **Problem:** "React has detected a change in the order of Hooks" error — `useRef` was placed after early returns (`if (authLoading)`, `if (isLoading)`, `if (error)`)
- **Fix:** Moved `useRef` before all conditional returns, initialized as `null`, set to `job.target_lang` on first data load using a null-check guard
- **File:** `frontend/src/app/jobs/[id]/page.tsx`

---

## Feature Additions

### Unclaimed Articles Page — Full Redesign
- **Replaced** the simple table with a filter-page-style UI: checkboxes, score column, pagination
- **Server-side pagination** with 10 / 100 / All toggle (offset/limit in API query) — necessary because unclaimed pool can reach tens of thousands of articles
- **Score ranking** — articles sorted by `rule_based_score` descending (highest quality first)
- **Multi-select checkboxes** — select individual articles or all on page
- **Delete Selected** + **Delete All** bulk actions
- **Removed** Languages and Chars Used columns from all views (mine/unclaimed/all)
- **Files:** `frontend/src/components/JobList.tsx` (major rewrite)

### Score Persistence
- **Problem:** Filter page sorts articles by score, but once submitted to PostgreSQL the ordering is lost
- **Fix:** Persisted `rule_based_score` into `extraction_metadata` JSONB at batch submission time
- **Functional index** added via Alembic migration 004: `CREATE INDEX ix_articles_rule_score ON articles (((extraction_metadata->>'rule_based_score')::float) DESC NULLS LAST)` — prevents full table scan on every unclaimed query
- **`list_unclaimed()` query** updated to `ORDER BY score DESC NULLS LAST, created_at DESC` with server-side `OFFSET`/`LIMIT`
- **Files:** `backend/app/services/article_service.py`, `backend/app/api/routes_articles.py`, `backend/app/repositories/job_repo.py`, `backend/alembic/versions/004_add_score_index.py`

### Bulk Delete (Selected + All)
- **New endpoints:** `POST /api/jobs/bulk-delete` and `DELETE /api/jobs/unclaimed-all`
- **Orphan article cleanup** optimized from O(N) individual queries to a single SQL query:
  ```sql
  DELETE FROM articles WHERE id IN (:ids) AND id NOT IN (SELECT article_id FROM translation_jobs)
  ```
  This fixed a critical timeout — the original loop was doing thousands of round-trips to Supabase, exceeding browser fetch timeout for large batches
- **Route ordering fix:** Moved static routes (`/jobs/bulk-delete`, `/jobs/unclaimed-all`) before parameterized `/jobs/{job_id}` — FastAPI matches routes top-to-bottom, so `unclaimed-all` was being caught by `{job_id}` and failing UUID validation (422)
- **Files:** `backend/app/api/routes_jobs.py`, `backend/app/repositories/job_repo.py`, `backend/app/repositories/article_repo.py`, `frontend/src/lib/api.ts`

### Language Selection Before Translation
- **Feature:** Language selector now appears on the CLAIMED job detail page before clicking Translate, instead of only after translation completes
- **Implementation:** `TranslationEditor` accepts `onLangChange` callback prop; `JobDetailPage` tracks selected language via `useRef` and passes it to `translateJob()` endpoint
- **Backend:** `POST /api/jobs/{job_id}/translate` now accepts optional `target_lang` in request body, updates `job.target_lang` before dispatching background task
- **Files:** `frontend/src/components/TranslationEditor.tsx`, `frontend/src/app/jobs/[id]/page.tsx`, `frontend/src/lib/api.ts`, `backend/app/api/routes_jobs.py`, `backend/app/schemas/job.py`

### Filter/Batching Page — Remove Language Toggle
- Removed `LanguageSelect` and `targetLang` state from the filter controls — language selection moved to per-article translation editor
- **File:** `frontend/src/app/filter/page.tsx`

### Shared ScoreBar Component
- Extracted `ScoreBar` from `filter/page.tsx` into `frontend/src/components/ScoreBar.tsx` for reuse in both filter page and unclaimed job list
- **Files:** `frontend/src/components/ScoreBar.tsx`, `frontend/src/app/filter/page.tsx`

### `GET /api/jobs` — Paginated Unclaimed Response
- Unclaimed view now returns `{ jobs: [...], total: N }` instead of a flat array, enabling frontend pagination controls
- Added `offset` and `limit` query params
- **File:** `backend/app/api/routes_jobs.py`, `backend/app/schemas/job.py`

### `source_url` in Job List Items
- Added `source_url` to `JobListItem` schema and response so unclaimed page can show clickable article links (matching filter page style)
- **Files:** `backend/app/schemas/job.py`, `backend/app/api/routes_jobs.py`, `frontend/src/lib/types.ts`

---

## Database & Performance

### Supabase SSL Fix
- **Problem:** Backend couldn't connect to Supabase — `SSLCertVerificationError: certificate verify failed: self-signed certificate in certificate chain`
- **Diagnosis:** Supabase's pooler uses a self-signed CA not in Python's trust store; full cert verification fails
- **Fix:** Changed to `ssl="require"` (asyncpg's equivalent of PostgreSQL `sslmode=require`) — encrypts the connection without cert verification, which is what Supabase recommends for pooler connections
- **File:** `backend/app/db.py`

### Statement Timeout & Idle Transaction Protection
- **Problem:** DELETE operations failed with `QueryCanceledError: canceling statement due to statement timeout`
- **Investigation:** 
  - First attempted `SET LOCAL statement_timeout` but couldn't use multiple SQL statements in one prepared statement (asyncpg limitation)
  - Traced the actual cause: a leaked "idle in transaction" session (pid 94003) was holding a lock on `translation_jobs`, blocking all DELETEs indefinitely — the query itself takes 87ms, the timeout was from waiting for the lock
- **Fix:** 
  - Added `command_timeout=30` to asyncpg connect args (client-side)
  - Created `set_session_timeouts()` helper that runs two separate `SET LOCAL` statements: `statement_timeout = '120000'` and `idle_in_transaction_session_timeout = '60000'`
  - Called at the start of every session: `get_db()`, and all background task sessions (`_run_extraction`, `_run_html_upgrade`, `_run_translation`, `recover_stale_jobs`)
  - Manually terminated the stuck session to unblock immediate deletes
- **File:** `backend/app/db.py`, `backend/app/api/routes_jobs.py`, `backend/app/api/routes_articles.py`, `backend/app/services/article_service.py`

### RLS Security Migration
- **Problem:** Supabase flagged all tables as publicly accessible — anyone with the anon key could query the database directly via PostgREST API, bypassing the FastAPI backend entirely
- **Analysis:** Backend connects as PostgreSQL superuser (unaffected by RLS). Only the Supabase REST API uses anon/authenticated roles. Enabling RLS with no policies blocks PostgREST entirely while leaving backend unaffected.
- **Fix:** Alembic migration 005 — `ALTER TABLE ... ENABLE ROW LEVEL SECURITY` + `FORCE ROW LEVEL SECURITY` on all 5 tables (articles, translation_jobs, final_translations, audit_logs, batches)
- **File:** `backend/alembic/versions/005_enable_rls.py`

---

## Debugging Sessions

### Delete All Timeout Investigation
- `DELETE FROM translation_jobs WHERE id IN (...)` timing out despite `statement_timeout = 30s` being set
- Discovered `SHOW statement_timeout` returned 30s but DELETE still timed out — realized 30s was *shorter* than Supabase's default 2 minutes, making it worse
- Connected directly via asyncpg to check `pg_stat_activity` — found pid 94003 stuck in "idle in transaction" for ~24 hours, holding a lock
- `pg_terminate_backend(94003)` immediately unblocked the DELETE (completed in 87ms)
- Root cause: a previous request had opened a transaction and never committed/closed it

### Bulk Delete O(N) Query Loop
- Original implementation looped through each orphan article individually (SELECT + SELECT + DELETE per article)
- With 3000+ articles, this was thousands of Supabase round-trips, easily exceeding browser fetch timeout
- Replaced with single `DELETE FROM articles WHERE id IN (...) AND id NOT IN (SELECT article_id FROM translation_jobs)` — entire cleanup in one query regardless of batch size

### Route Ordering (FastAPI Path Conflict)
- `DELETE /api/jobs/unclaimed-all` returned 422 Unprocessable Entity
- Root cause: FastAPI matched it against `DELETE /api/jobs/{job_id}` first, tried to parse "unclaimed-all" as UUID, failed
- Same issue affected `POST /api/jobs/bulk-delete`
- Fix: moved static routes before `{job_id}` routes in the file

---

## Infrastructure & Documentation

### Production Readiness Plan
- Rewrote `PRODUCTION_READINESS_PLAN.md` from a generic checklist into a concrete 6-phase roadmap:
  - Phase 1: Deploy to Railway (fix Dockerfiles, env vars, CORS)
  - Phase 2: GCP bucket for published articles (blocked on credentials)
  - Phase 3: RSS feed ingestion (blocked on feed URL)
  - Phase 4: Hardening (security, reliability, monitoring)
  - Phase 5: Testing
  - Phase 6: Handoff (Railway transfer, Supabase transfer, DeepL key swap)

### GCP & RSS Architecture Discussions
- Clarified that GCP bucket integration requires: `google-cloud-storage` library, Service Account key, bucket name, agreed output format (JSON per article)
- Analyzed RSS ingestion feasibility: `feedparser` library, maps to existing `submit_batch()` pipeline, inherits URL deduplication — ~2-3 hours of work once feed URL is available
- Advised on what to ask manager: RSS feed URL, output format for published articles, bucket purpose (input vs output)

### Embedding/ML Model Latency Analysis
- Analyzed `model_v2 (1).ipynb` — XGBoost model with `all-MiniLM-L6-v2` sentence transformer embeddings (384-dim body + 384-dim title = 768 embedding dims + 9 tabular features = 777 total)
- Identified bottleneck: SentenceTransformer encoding is 5-20 min on CPU for 10k articles; XGBoost inference itself is < 1 second
- Recommended: use OpenAI `text-embedding-3-small` API (~10 cents per 10k articles, 30-60 seconds) — but requires retraining XGBoost on new embedding space
- Alternative: pre-compute embeddings at upload time as background task

---

## Types & API Contracts Updated

- `JobListItem` — added `source_url`, `rule_based_score`
- `JobListResponse` — new wrapper type `{ jobs: JobListItem[], total: number }` for paginated unclaimed view
- `TranslateRequest` — new schema accepting optional `target_lang`
- `BulkDeleteRequest` — new schema accepting `job_ids: list[uuid.UUID]`
- `frontend/src/lib/types.ts` — synced all new fields
- `frontend/src/lib/api.ts` — added `deleteJobsBulk()`, `deleteAllUnclaimed()`, `fetchUnclaimedJobs()`, updated `translateJob()` to accept `targetLang`
