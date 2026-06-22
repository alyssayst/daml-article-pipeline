import logging

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import CurrentUser, get_current_user
from app.config import SUPPORTED_TARGET_LANG_CODES
from app.db import get_db
from app.repositories.article_repo import ArticleRepository
from app.schemas.article import ArticleResponse, ArticleSubmitRequest, BatchSubmitRequest
from app.schemas.job import JobResponse
from app.services.article_service import ArticleService
from app.services.translator_service import TranslatorService

logger = logging.getLogger(__name__)
router = APIRouter(tags=["articles"])


@router.post("/articles", response_model=JobResponse, status_code=201)
async def submit_article(
    request: ArticleSubmitRequest,
    background_tasks: BackgroundTasks,
    user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Submit a URL for translation.

    Creates article + job (PENDING), kicks off background extraction + translation,
    and returns the job immediately.
    """
    service = ArticleService()
    source_url = str(request.source_url)

    # Validate target_lang if provided
    if request.target_lang and request.target_lang not in SUPPORTED_TARGET_LANG_CODES:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported target language: {request.target_lang}",
        )

    try:
        job, is_new = await service.submit_url(
            source_url,
            db,
            target_lang=request.target_lang,
            nordot_link=str(request.nordot_link) if request.nordot_link else None,
            created_by=user.user_id,
        )
    except Exception as e:
        logger.exception("Failed to submit URL %s", source_url)
        raise HTTPException(status_code=400, detail=str(e))

    if is_new:
        # Dispatch background pipeline
        background_tasks.add_task(_run_extraction, job.id)

    return job


async def _run_extraction(job_id):
    """Background task that runs article extraction with its own DB session."""
    from app.db import async_session_factory, set_session_timeouts

    async with async_session_factory() as db:
        await set_session_timeouts(db)
        service = TranslatorService(db)
        await service.extract_article(job_id)


@router.get("/articles", response_model=list[ArticleResponse])
async def list_articles(
    source_url: str | None = Query(None, description="Filter by source URL"),
    user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Lookup articles, optionally filtered by source_url for dedupe checks."""
    repo = ArticleRepository(db)

    if source_url:
        article = await repo.get_by_url(source_url)
        return [article] if article else []

    # For MVP, don't return all articles — require a filter
    raise HTTPException(
        status_code=400,
        detail="Provide source_url query parameter to search articles",
    )


@router.post("/articles/batch", status_code=201)
async def submit_article_batch(
    request: BatchSubmitRequest,
    background_tasks: BackgroundTasks,
    user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Submit multiple URLs for translation in one request."""
    if request.target_lang and request.target_lang not in SUPPORTED_TARGET_LANG_CODES:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported target language: {request.target_lang}",
        )

    service = ArticleService()

    # ── Batch mode: look up full article data from batch store ──
    if request.batch_id:
        from app.batch_store import get_batch

        batch = get_batch(request.batch_id)
        if batch is None:
            raise HTTPException(status_code=404, detail="Batch not found or expired")

        # Build URL set from the request for matching
        selected_urls: set[str] = set()
        if request.urls:
            selected_urls = {str(u) for u in request.urls}
        elif request.articles:
            selected_urls = {str(a.source_url) for a in request.articles}

        # Look up full article data from batch store.
        # Also check filter_results for scores (scored articles have rule_based_score).
        filter_results = batch.get("filter_results")
        score_by_url: dict[str, float] = {}
        if filter_results:
            for fa in filter_results:
                u = fa.get("source_url")
                if u:
                    score_by_url[u] = fa.get("rule_based_score", 0.0)

        articles_data = []
        for article in batch["articles"]:
            url = article.get("source_url")
            if url and url in selected_urls:
                # Attach score from filter results if available
                if url in score_by_url:
                    article["rule_based_score"] = score_by_url[url]
                articles_data.append(article)

        if not articles_data:
            raise HTTPException(status_code=400, detail="No matching articles found in batch")

        count = await service.submit_batch(
            articles_data, db,
            target_lang=request.target_lang,
            created_by=user.user_id,
        )
        return {"created": count, "batch_id": request.batch_id}

    # ── Legacy mode: individual URL submissions with extraction ──
    jobs: list = []

    if request.articles:
        for item in request.articles:
            try:
                job, is_new = await service.submit_url(
                    str(item.source_url),
                    db,
                    target_lang=request.target_lang,
                    nordot_link=str(item.nordot_link) if item.nordot_link else None,
                    created_by=user.user_id,
                )
                jobs.append(job)
                if is_new:
                    background_tasks.add_task(_run_extraction, job.id)
            except Exception as e:
                logger.warning("Skipping URL %s: %s", item.source_url, e)
                continue
    else:
        for url in request.urls or []:
            try:
                job, is_new = await service.submit_url(
                    str(url), db, target_lang=request.target_lang,
                    created_by=user.user_id,
                )
                jobs.append(job)
                if is_new:
                    background_tasks.add_task(_run_extraction, job.id)
            except Exception as e:
                logger.warning("Skipping URL %s: %s", url, e)
                continue

    return jobs
