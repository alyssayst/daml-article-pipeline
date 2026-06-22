import logging
import uuid

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import CurrentUser, get_current_user
from app.config import SUPPORTED_TARGET_LANG_CODES
from app.db import get_db
from app.repositories.job_repo import JobRepository
from app.repositories.article_repo import ArticleRepository
from app.repositories.audit_repo import AuditRepository
from app.repositories.translation_repo import TranslationRepository
from app.schemas.job import (
    DraftContent,
    DraftSaveRequest,
    JobDetailResponse,
    JobListItem,
    JobListResponse,
    JobResponse,
    RetranslateRequest,
    TranslateRequest,
)
from app.schemas.article import ArticleResponse
from app.schemas.job import FinalTranslationResponse
from app.services.article_service import ArticleService

logger = logging.getLogger(__name__)
router = APIRouter(tags=["jobs"])


def _job_to_list_item(job) -> JobListItem:
    article = job.article
    meta = article.extraction_metadata if article else None
    score = meta.get("rule_based_score") if meta else None
    return JobListItem(
        id=job.id,
        status=job.status,
        source_lang=job.source_lang,
        target_lang=job.target_lang,
        source_title=article.source_title if article else None,
        source_url=article.source_url if article else None,
        error_message=job.error_message,
        deepl_chars_used=job.deepl_chars_used,
        rule_based_score=float(score) if score is not None else None,
        claimed_by=job.claimed_by,
        claimed_at=job.claimed_at,
        created_at=job.created_at,
        updated_at=job.updated_at,
        published_at=job.published_at,
        exported_at=job.exported_at,
    )


@router.get("/jobs")
async def list_jobs(
    view: str = Query("mine", description="View mode: mine, unclaimed, all, published"),
    offset: int = Query(0, ge=0),
    limit: int = Query(50000, ge=1, le=50000),
    exported: bool = Query(False, description="For view=published: false=ready, true=already exported"),
    user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List translation jobs. Use ?view=mine (default), ?view=unclaimed, ?view=all, or ?view=published."""
    repo = JobRepository(db)

    if view == "unclaimed":
        jobs, total = await repo.list_unclaimed(offset=offset, limit=limit)
        return JobListResponse(
            jobs=[_job_to_list_item(j) for j in jobs],
            total=total,
        )

    if view == "published":
        jobs, total = await repo.list_published(offset=offset, limit=limit)
        return JobListResponse(
            jobs=[_job_to_list_item(j) for j in jobs],
            total=total,
        )

    if view == "all":
        jobs = await repo.list_all()
    else:
        jobs = await repo.list_for_user(user.user_id)

    return [_job_to_list_item(j) for j in jobs]


@router.get("/jobs/{job_id}", response_model=JobDetailResponse)
async def get_job_detail(
    job_id: uuid.UUID,
    user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get full job detail with article, draft, and final versions."""
    repo = JobRepository(db)
    job = await repo.get_by_id(job_id)

    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")

    article = job.article
    if article is None:
        raise HTTPException(status_code=404, detail="Article not found for job")

    # Resolve draft: human edits if available, else AI output
    draft = DraftContent(
        title=job.draft_title if job.draft_title else job.ai_title,
        body=job.draft_body if job.draft_body else job.ai_body,
    )

    final_versions = [
        FinalTranslationResponse.model_validate(ft)
        for ft in (job.final_translations or [])
    ]
    # Sort by version descending
    final_versions.sort(key=lambda x: x.version, reverse=True)

    return JobDetailResponse(
        job=JobResponse.model_validate(job),
        article=ArticleResponse.model_validate(article),
        draft=draft,
        final_versions=final_versions,
    )


@router.put("/jobs/{job_id}/draft", response_model=JobResponse)
async def save_draft(
    job_id: uuid.UUID,
    request: DraftSaveRequest,
    user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Save human-edited translation draft."""
    service = ArticleService()
    try:
        job = await service.save_draft(
            job_id, request.draft_title, request.draft_body, db,
            user_id=user.user_id,
        )
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return job


@router.post("/jobs/{job_id}/publish", response_model=JobResponse)
async def publish_job(
    job_id: uuid.UUID,
    user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Publish: copy draft to final_translations and mark PUBLISHED."""
    service = ArticleService()
    try:
        job = await service.publish(job_id, db, user_id=user.user_id)
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return job


@router.post("/jobs/{job_id}/retry", response_model=JobResponse)
async def retry_job(
    job_id: uuid.UUID,
    background_tasks: BackgroundTasks,
    user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Retry a failed job: clear error, reset to PENDING, re-dispatch."""
    service = ArticleService()
    try:
        job = await service.retry_job(job_id, db, user_id=user.user_id)
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    # Re-dispatch background translation
    from app.api.routes_articles import _run_extraction

    background_tasks.add_task(_run_extraction, job.id)

    return job


@router.post("/jobs/{job_id}/retranslate", response_model=JobResponse)
async def retranslate_job(
    job_id: uuid.UUID,
    request: RetranslateRequest,
    background_tasks: BackgroundTasks,
    user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Re-translate a job with a different target language."""
    if request.target_lang not in SUPPORTED_TARGET_LANG_CODES:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported target language: {request.target_lang}",
        )

    service = ArticleService()
    try:
        job = await service.retranslate(job_id, request.target_lang, db, user_id=user.user_id)
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    # Re-dispatch background translation
    from app.api.routes_articles import _run_extraction

    background_tasks.add_task(_run_extraction, job.id)

    return job


class BulkDeleteRequest(BaseModel):
    job_ids: list[uuid.UUID]


@router.post("/jobs/bulk-delete", status_code=204)
async def bulk_delete_jobs(
    request: BulkDeleteRequest,
    user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Delete multiple jobs by ID. Cleans up orphan articles."""
    job_repo = JobRepository(db)
    article_repo = ArticleRepository(db)
    translation_repo = TranslationRepository(db)
    audit_repo = AuditRepository(db)

    # Verify ownership: only unclaimed jobs or jobs owned by the user
    for jid in request.job_ids:
        job = await job_repo.get_by_id(jid)
        if job and job.claimed_by and job.claimed_by != user.user_id:
            raise HTTPException(
                status_code=403,
                detail=f"You do not own job {jid}",
            )

    for jid in request.job_ids:
        await translation_repo.delete_by_job_id(jid)

    article_ids = await job_repo.delete_by_ids(request.job_ids)
    await article_repo.delete_orphans(article_ids)

    await audit_repo.log(
        "JOBS_BULK_DELETED",
        {"count": len(request.job_ids)},
        user_id=user.user_id,
    )
    await db.commit()
    return None


@router.delete("/jobs/unclaimed-all", status_code=204)
async def delete_all_unclaimed(
    user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Delete all unclaimed EXTRACTED jobs. Cleans up orphan articles."""
    job_repo = JobRepository(db)
    article_repo = ArticleRepository(db)
    audit_repo = AuditRepository(db)

    article_ids = await job_repo.delete_unclaimed_all()
    cleaned = await article_repo.delete_orphans(article_ids)

    await audit_repo.log(
        "UNCLAIMED_ALL_DELETED",
        {"jobs_deleted": len(article_ids), "articles_cleaned": cleaned},
        user_id=user.user_id,
    )
    await db.commit()
    return None


@router.delete("/jobs/{job_id}", status_code=204)
async def delete_job(
    job_id: uuid.UUID,
    user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Delete a job and related final translations. Deletes article if unused."""
    job_repo = JobRepository(db)
    translation_repo = TranslationRepository(db)
    article_repo = ArticleRepository(db)
    audit_repo = AuditRepository(db)

    job = await job_repo.get_by_id(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")

    if job.claimed_by and job.claimed_by != user.user_id:
        raise HTTPException(status_code=403, detail="You do not own this job")

    article_id = job.article_id

    await translation_repo.delete_by_job_id(job_id)
    await job_repo.delete(job)

    # Clean up orphan article if this was its only job.
    if not await article_repo.has_jobs(article_id):
        article = await article_repo.get_by_id(article_id)
        if article is not None:
            await article_repo.delete(article)

    await audit_repo.log("JOB_DELETED", {"job_id": str(job_id)}, user_id=user.user_id)
    await db.commit()
    return None


@router.post("/jobs/{job_id}/claim", response_model=JobResponse)
async def claim_job(
    job_id: uuid.UUID,
    background_tasks: BackgroundTasks,
    user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Atomically claim an EXTRACTED job."""
    job_repo = JobRepository(db)
    audit_repo = AuditRepository(db)

    job = await job_repo.claim(job_id, user.user_id)
    if job is None:
        raise HTTPException(
            status_code=409,
            detail="Job is not available for claiming (already claimed or not in EXTRACTED status)",
        )

    await audit_repo.log(
        "JOB_CLAIMED",
        {"job_id": str(job_id)},
        user_id=user.user_id,
    )
    await db.commit()

    # Fire background HTML extraction upgrade (non-blocking)
    async def _run_html_upgrade(jid):
        from app.db import async_session_factory, set_session_timeouts
        from app.services.translator_service import TranslatorService as TS

        async with async_session_factory() as session:
            await set_session_timeouts(session)
            svc = TS(session)
            await svc.upgrade_html(jid)

    background_tasks.add_task(_run_html_upgrade, job_id)

    return job


@router.post("/jobs/{job_id}/unclaim", response_model=JobResponse)
async def unclaim_job(
    job_id: uuid.UUID,
    user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Release a claim on a job. Only the current claimant can unclaim."""
    job_repo = JobRepository(db)
    audit_repo = AuditRepository(db)

    job = await job_repo.unclaim(job_id, user.user_id)
    if job is None:
        raise HTTPException(
            status_code=409,
            detail="Cannot unclaim: you are not the claimant or job is not in CLAIMED status",
        )

    await audit_repo.log(
        "JOB_UNCLAIMED",
        {"job_id": str(job_id)},
        user_id=user.user_id,
    )
    await db.commit()
    return job


@router.post("/jobs/{job_id}/translate", response_model=JobResponse)
async def translate_job(
    job_id: uuid.UUID,
    background_tasks: BackgroundTasks,
    request: TranslateRequest | None = None,
    user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Trigger DeepL translation on a CLAIMED job. Only the claimant can translate."""
    job_repo = JobRepository(db)
    audit_repo = AuditRepository(db)

    job = await job_repo.get_by_id(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")

    if job.status != "CLAIMED":
        raise HTTPException(
            status_code=400,
            detail=f"Cannot translate: job is in {job.status} status, must be CLAIMED",
        )

    if job.claimed_by != user.user_id:
        raise HTTPException(
            status_code=403,
            detail="Only the claimant can trigger translation",
        )

    # Update target language if specified
    if request and request.target_lang:
        from app.config import SUPPORTED_TARGET_LANG_CODES
        if request.target_lang not in SUPPORTED_TARGET_LANG_CODES:
            raise HTTPException(
                status_code=400,
                detail=f"Unsupported target language: {request.target_lang}",
            )
        job.target_lang = request.target_lang
        await job_repo.update(job)
        await db.commit()

    await audit_repo.log(
        "TRANSLATION_REQUESTED",
        {"job_id": str(job_id)},
        user_id=user.user_id,
    )
    await db.commit()

    async def _run_translation(jid, uid):
        from app.db import async_session_factory, set_session_timeouts
        from app.services.translator_service import TranslatorService as TS

        async with async_session_factory() as session:
            await set_session_timeouts(session)
            svc = TS(session)
            await svc.translate_job(jid, user_id=uid)

    background_tasks.add_task(_run_translation, job.id, user.user_id)

    return job
