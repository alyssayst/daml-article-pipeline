import asyncio
import logging
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from pydantic import BaseModel

from app.auth import CurrentUser, get_current_user
from app.batch_store import (
    get_batch,
    get_filter_results,
    get_ml_status,
    set_filter_results,
    set_ml_status,
)
from app.services.enhanced_filter_service import get_filter

logger = logging.getLogger(__name__)
router = APIRouter(tags=["filter"])


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

class FilterApplyRequest(BaseModel):
    batch_id: str
    threshold_percentile: int = 70
    weights: dict[str, float] | None = None
    use_ml: bool = False


class FilterApplyResponse(BaseModel):
    batch_id: str
    total_before: int
    total_after: int
    threshold_score: float
    ml_status: str = "idle"


class ScoredArticleResponse(BaseModel):
    title: str | None = None
    source_url: str | None = None
    nordot_link: str | None = None
    body_text_preview: str | None = None
    published_at: str | None = None
    word_count: int = 0
    rule_based_score: float = 0.0
    length_score: float = 0.0
    freshness_score: float = 0.0
    hook_score: float = 0.0
    title_penalty: float = 1.0
    meets_min_requirements: bool = False
    excluded_reason: str | None = None
    freshness_days: float = 0.0
    predicted_views: int | None = None


class FilterResultsResponse(BaseModel):
    articles: list[ScoredArticleResponse]
    total: int
    offset: int
    limit: int
    ml_status: str = "idle"


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.post("/filter/apply", response_model=FilterApplyResponse)
async def apply_filter(
    request: FilterApplyRequest,
    background_tasks: BackgroundTasks,
    user: CurrentUser = Depends(get_current_user),
):
    """Score and filter articles in a batch using rule-based criteria."""
    batch = get_batch(request.batch_id)
    if batch is None:
        raise HTTPException(status_code=404, detail="Batch not found or expired")

    articles = batch["articles"]
    if not articles:
        raise HTTPException(status_code=400, detail="Batch contains no articles")

    filt = get_filter("enhanced_rule_based")
    scored = filt.score(articles, weights=request.weights)
    filtered, threshold_score = filt.apply_threshold(scored, request.threshold_percentile)

    set_filter_results(request.batch_id, filtered)

    ml_status = "idle"
    if request.use_ml:
        set_ml_status(request.batch_id, "processing")
        ml_status = "processing"
        background_tasks.add_task(_run_ml_scoring, request.batch_id)

    return FilterApplyResponse(
        batch_id=request.batch_id,
        total_before=len(articles),
        total_after=len(filtered),
        threshold_score=threshold_score,
        ml_status=ml_status,
    )


async def _run_ml_scoring(batch_id: str):
    """Background task: run ML scoring on filter results."""
    try:
        from app.services.ml_scorer import ml_scorer

        results = get_filter_results(batch_id)
        if results is None:
            set_ml_status(batch_id, "failed")
            return

        # ML scoring is CPU-bound — run in thread pool
        scored = await asyncio.to_thread(ml_scorer.score, results)
        set_filter_results(batch_id, scored)
        set_ml_status(batch_id, "ready")
        logger.info("ML scoring complete for batch %s (%d articles)", batch_id, len(scored))
    except Exception:
        logger.exception("ML scoring failed for batch %s", batch_id)
        set_ml_status(batch_id, "failed")


def _to_scored_response(a: dict[str, Any]) -> ScoredArticleResponse:
    body = a.get("body_text") or ""
    preview = body[:200] + "..." if len(body) > 200 else body

    pub = a.get("published_at")
    pub_str = str(pub) if pub is not None else None

    return ScoredArticleResponse(
        title=a.get("title"),
        source_url=a.get("source_url"),
        nordot_link=a.get("nordot_link"),
        body_text_preview=preview,
        published_at=pub_str,
        word_count=a.get("word_count", 0),
        rule_based_score=a.get("rule_based_score", 0.0),
        length_score=a.get("length_score", 0.0),
        freshness_score=a.get("freshness_score", 0.0),
        hook_score=a.get("hook_score", 0.0),
        title_penalty=a.get("title_penalty", 1.0),
        meets_min_requirements=a.get("meets_min_requirements", False),
        excluded_reason=a.get("excluded_reason"),
        freshness_days=a.get("freshness_days", 0.0),
        predicted_views=a.get("predicted_views"),
    )


@router.get("/filter/results/{batch_id}", response_model=FilterResultsResponse)
async def filter_results(
    batch_id: str,
    offset: int = Query(0, ge=0),
    limit: int = Query(10, ge=1, le=50000),
    user: CurrentUser = Depends(get_current_user),
):
    """Return paginated filtered results for a batch."""
    results = get_filter_results(batch_id)
    if results is None:
        raise HTTPException(
            status_code=404,
            detail="No filter results found. Run /filter/apply first.",
        )

    total = len(results)
    page = results[offset : offset + limit]

    return FilterResultsResponse(
        articles=[_to_scored_response(a) for a in page],
        total=total,
        offset=offset,
        limit=limit,
        ml_status=get_ml_status(batch_id),
    )
