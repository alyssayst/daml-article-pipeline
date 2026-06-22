import csv
import io
import re
import uuid
import zipfile
from datetime import date
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user
from app.db import get_db
from app.repositories.job_repo import JobRepository

router = APIRouter(prefix="/exports", tags=["exports"])

_TAG_RE = re.compile(r"<[^>]+>")


def _strip_html(html: str) -> str:
    return _TAG_RE.sub("", html)


def _word_count(html: str) -> int:
    return len(_strip_html(html).split())


def _render_html(row: dict[str, Any]) -> str:
    from html import escape
    source_url = row.get("source_url") or ""
    published_at = row.get("published_at")
    pub_str = published_at.strftime("%Y-%m-%d") if published_at else "unknown"
    final_title = row.get("final_title") or ""
    final_body = row.get("final_body") or ""

    safe_url = source_url if source_url.startswith(("http://", "https://")) else ""
    link = (
        f'<a href="{escape(safe_url)}">{escape(safe_url)}</a>'
        if safe_url
        else "(no source URL)"
    )
    return (
        f"<h1>{escape(final_title)}</h1>\n"
        f"<p><em>Source: {link} | Translated: {escape(pub_str)}</em></p>\n"
        f"<hr>\n"
        f"{final_body}"
    )


class ExportRequest(BaseModel):
    job_ids: list[uuid.UUID] | None = None


class MarkDoneRequest(BaseModel):
    job_ids: list[uuid.UUID]


@router.post("/published")
async def export_published(
    body: ExportRequest,
    db: AsyncSession = Depends(get_db),
    _current_user=Depends(get_current_user),
):
    """Download a ZIP of published articles.

    Pass job_ids to export specific articles, or omit to export all published.
    Downloading never changes exported_at — that is a separate manual action.
    """
    repo = JobRepository(db)
    rows = await repo.list_published_for_export(job_ids=body.job_ids)

    if not rows:
        raise HTTPException(status_code=404, detail="No published articles found to export.")

    zip_buf = io.BytesIO()
    with zipfile.ZipFile(zip_buf, "w", zipfile.ZIP_DEFLATED) as zf:
        csv_buf = io.StringIO()
        fieldnames = [
            "job_id",
            "article_id",
            "version",
            "source_url",
            "source_title",
            "published_title",
            "source_lang",
            "target_lang",
            "published_at",
            "translated_by",
            "html_filename",
            "word_count",
        ]
        writer = csv.DictWriter(csv_buf, fieldnames=fieldnames)
        writer.writeheader()

        for row in rows:
            filename = f"articles/{row['job_id']}_v{row['version']}.html"
            published_at = row.get("published_at")

            writer.writerow({
                "job_id": str(row["job_id"]),
                "article_id": str(row["article_id"]),
                "version": row["version"],
                "source_url": row.get("source_url") or "",
                "source_title": row.get("source_title") or "",
                "published_title": row.get("final_title") or "",
                "source_lang": row.get("source_lang") or "",
                "target_lang": row.get("target_lang") or "",
                "published_at": published_at.isoformat() if published_at else "",
                "translated_by": str(row["translated_by"]) if row.get("translated_by") else "",
                "html_filename": filename,
                "word_count": _word_count(row.get("final_body") or ""),
            })

            zf.writestr(filename, _render_html(row))

        zf.writestr("manifest.csv", csv_buf.getvalue())

    zip_buf.seek(0)
    count = len(rows)
    filename = f"export_{date.today()}_{count}articles.zip"
    return StreamingResponse(
        zip_buf,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/count")
async def export_count(
    db: AsyncSession = Depends(get_db),
    _current_user=Depends(get_current_user),
):
    """Return count of published articles not yet confirmed as exported."""
    repo = JobRepository(db)
    ready = await repo.count_unexported()
    return {"ready": ready}


@router.post("/mark-done")
async def mark_done(
    body: MarkDoneRequest,
    db: AsyncSession = Depends(get_db),
    _current_user=Depends(get_current_user),
):
    """Confirm that the specified articles have been published to the live platform.

    This sets exported_at on each job. This is a manual human confirmation —
    downloading a ZIP does not trigger this automatically.
    """
    repo = JobRepository(db)
    marked = await repo.mark_as_exported(body.job_ids)
    return {"marked": marked}


@router.post("/unmark-done")
async def unmark_done(
    body: MarkDoneRequest,
    db: AsyncSession = Depends(get_db),
    _current_user=Depends(get_current_user),
):
    """Return articles to the 'Ready to Export' queue by clearing exported_at."""
    repo = JobRepository(db)
    unmarked = await repo.unmark_as_exported(body.job_ids)
    return {"unmarked": unmarked}
