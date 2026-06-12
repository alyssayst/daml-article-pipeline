# Production Readiness Plan — Translation Platform

From current state → deployed → feature-complete → handed off.

---

## Phase 1: Deploy to Railway (~2-3 hours)

### 1.1 Fix Dockerfiles for production
- [ ] **`frontend/Dockerfile`** — change `npm run dev` → `npm run build` + `node .next/standalone/server.js`
- [ ] **`backend/Dockerfile`** — remove `--reload` from uvicorn command

### 1.2 Create Railway project
- [ ] Create project on personal Railway account (transferable later)
- [ ] Two services: `frontend` and `backend`
- [ ] Connect GitHub repo, set root directories (`translation-platform/frontend`, `translation-platform/backend`)

### 1.3 Set environment variables

**Backend service:**
- `DATABASE_URL` — Supabase async pooler (port 6543)
- `DATABASE_URL_SYNC` — Supabase direct connection (port 5432, for migrations)
- `DEEPL_API_KEY`
- `SUPABASE_URL`
- `SUPABASE_JWT_SECRET`

**Frontend service:**
- `NEXT_PUBLIC_SUPABASE_URL`
- `NEXT_PUBLIC_SUPABASE_ANON_KEY`
- `NEXT_PUBLIC_API_URL` — backend's Railway public URL

### 1.4 Security tightening
- [ ] CORS: change `allow_origins=["*"]` → frontend's Railway domain (`backend/app/main.py`)
- [ ] Supabase: add production redirect URL in Supabase Auth settings
- [ ] SSL: already handled by Railway (automatic HTTPS)
- [ ] DB connection: `ssl="require"` already configured for Supabase pooler

### 1.5 Deploy + smoke test
- [ ] Push to trigger deploys
- [ ] Test full flow: login → upload CSV → filter → submit → claim → translate → publish
- [ ] Verify auth works (JWT verification against Supabase)
- [ ] Custom domain (optional)

---

## Phase 2: GCP Bucket for Published Articles (~1 hour)

**Blocked on:** Service Account key + bucket name from manager

### 2.1 Implementation
- [ ] Add `google-cloud-storage` to `backend/requirements.txt`
- [ ] Add `GCP_BUCKET_NAME` and `GOOGLE_APPLICATION_CREDENTIALS` env vars
- [ ] Add upload call in `ArticleService.publish()` — after DB commit, push JSON to bucket
- [ ] Add `GCP_BUCKET_NAME` to Railway env vars
- [ ] Upload Service Account key JSON to Railway (via env var or file mount)

### 2.2 Output format (confirm with manager)
- File per article: `articles/{job_id}.json`
- Fields: title, body, source_url, source_lang, target_lang, published_at, version

---

## Phase 3: RSS Feed Ingestion (~2-3 hours)

**Blocked on:** RSS feed URL + sample payload from manager

### 3.1 Implementation
- [ ] Add `feedparser` to `backend/requirements.txt`
- [ ] New endpoint: `POST /api/ingest/rss` — fetches RSS URL, parses XML, maps to article schema
- [ ] Map RSS fields → existing `submit_batch()` pipeline (same code path as CSV)
- [ ] Option A: Manual trigger (button in UI: "Fetch from RSS")
- [ ] Option B: Scheduled cron (Railway supports cron jobs)
- [ ] Deduplication: skip articles already in DB by source_url (already implemented in `submit_batch`)

### 3.2 Verify
- [ ] RSS ingest produces same result as CSV upload for the same articles
- [ ] Duplicate articles are skipped

---

## Phase 4: Hardening (do before real users)

### 4.1 Security
- [ ] Auth: JWT verification matches Supabase (algorithm, audience) — already done (ES256 via JWKS)
- [ ] Ownership checks on mutate operations — already done (claim, draft, publish, translate)
- [ ] No secrets in code or logs
- [ ] File upload: size limits on CSV upload endpoint
- [ ] Dependencies: run `pip audit` and `npm audit`, fix criticals

### 4.2 Reliability
- [ ] External call timeouts: DeepL, article extraction — already have semaphore + timeouts
- [ ] Idle-in-transaction timeout — already set (60s, prevents lock buildup)
- [ ] Stale job recovery on startup — already implemented
- [ ] Add `GET /health` endpoint (simple DB ping for monitoring)

### 4.3 Monitoring
- [ ] Railway provides built-in logs — sufficient for v1
- [ ] Optional: add Sentry for error tracking (backend + frontend)
- [ ] Check Supabase dashboard for connection pool usage

### 4.4 Database
- [ ] Supabase handles backups automatically (point-in-time recovery on paid plan)
- [ ] Migrations run on startup via `alembic upgrade head` — acceptable for single-instance deploy
- [ ] Functional index on `rule_based_score` — already created (migration 004)

---

## Phase 5: Testing (nice to have before handoff)

### 5.1 Minimum useful tests
- [ ] ~5-10 backend API tests: auth required routes return 401, claim rejects wrong user, publish creates version
- [ ] Run with: `pytest` + `httpx.AsyncClient` + test DB override
- [ ] CI: GitHub Actions runs tests on push (`.github/workflows/ci.yml`)

### 5.2 Frontend
- [ ] `npm run build` passes (catches type errors, import issues)
- [ ] Optional: 1-2 Playwright E2E tests for critical flows

---

## Phase 6: Handoff

### 6.1 Accounts to transfer

| What | How | Time |
|---|---|---|
| **Railway project** | Settings → Transfer to their team account | 5 min |
| **Supabase project** | Project Settings → Transfer to their org | 5 min |
| **DeepL API key** | They create own account, swap key in Railway env var | 1 min |
| **Domain DNS** | They point their domain to Railway | 5 min |
| **GitHub repo** | Transfer repo or add their org as collaborator | 5 min |

### 6.2 Before handoff, they need
- Railway team account (created)
- Supabase org (to receive transfer)
- DeepL API account (free tier: 500k chars/mo, pro: $5.49/mo)
- DNS access for their domain

### 6.3 Documentation to deliver
- [ ] How to deploy: push to GitHub → Railway auto-deploys
- [ ] Env vars list with descriptions
- [ ] How to view logs / restart / rollback (Railway dashboard)
- [ ] How to manage users (Supabase Auth dashboard)
- [ ] How to run locally (docker compose up)
- [ ] Link to `ENGINEERING_LESSONS.md` and this plan

### 6.4 Zero-downtime transfer
- Railway: config, domains, deploy history all transfer with the project
- Supabase: database, auth users, API keys all transfer
- DeepL: just a key swap, no data migration

---

## Quick Reference: What's Already Done vs. What's Left

### Already done
- Multi-user auth (Supabase ES256 JWT)
- Claim/translate/publish workflow
- Bulk CSV upload + filter pipeline
- Lazy HTML extraction on claim
- Bulk delete (optimized single-query orphan cleanup)
- Server-side pagination for unclaimed articles
- Score-based ranking with functional index
- SSL connection to Supabase (`ssl="require"`)
- Session timeout protection (idle-in-transaction killer)

### Left to do
1. **Deploy to Railway** — fix Dockerfiles, set env vars, deploy (~2-3 hours)
2. **GCP bucket publish** — waiting on credentials (~1 hour once unblocked)
3. **RSS ingestion** — waiting on feed URL (~2-3 hours once unblocked)
4. **Security tightening** — CORS lockdown, audit (~1 hour)
5. **Handoff** — transfer accounts, write docs (~2 hours)

**Total remaining: ~8-10 hours of work, spread across whenever blockers clear.**
