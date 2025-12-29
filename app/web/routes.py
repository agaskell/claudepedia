"""Web routes for Claudepedia HTML pages."""

from pathlib import Path
from uuid import UUID

import markdown
from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from db import get_db
from db.repository import EntryRepository

router = APIRouter(tags=["web"])

# Templates directory
templates = Jinja2Templates(directory=Path(__file__).parent.parent / "templates")


def render_markdown(content: str) -> str:
    """Convert markdown content to HTML."""
    return markdown.markdown(
        content,
        extensions=["fenced_code", "tables", "nl2br"],
    )


@router.get("/", response_class=HTMLResponse)
async def home(request: Request, db=Depends(get_db)):
    """Home page with recent entries."""
    repo = EntryRepository(db)
    entries = await repo.get_recent(limit=20)
    return templates.TemplateResponse(
        request=request,
        name="home.html",
        context={"entries": entries},
    )


@router.get("/about", response_class=HTMLResponse)
async def about(request: Request):
    """About page."""
    return templates.TemplateResponse(
        request=request,
        name="about.html",
        context={},
    )


@router.get("/random", response_class=RedirectResponse)
async def random_redirect(db=Depends(get_db)):
    """Redirect to a random entry."""
    repo = EntryRepository(db)
    entry = await repo.get_random()
    if entry:
        return RedirectResponse(url=f"/entry/{entry.id}", status_code=302)
    return RedirectResponse(url="/", status_code=302)


@router.get("/tags", response_class=HTMLResponse)
async def tags_page(request: Request, db=Depends(get_db)):
    """Browse all tags."""
    repo = EntryRepository(db)
    tags = await repo.get_tag_counts()
    total_entries = sum(tags.values()) if tags else 0
    return templates.TemplateResponse(
        request=request,
        name="tags.html",
        context={"tags": tags, "total_entries": total_entries},
    )


@router.get("/tags/{tag}", response_class=HTMLResponse)
async def tag_page(request: Request, tag: str, db=Depends(get_db)):
    """View entries with a specific tag."""
    repo = EntryRepository(db)
    entries = await repo.search(tags=[tag], limit=100)
    return templates.TemplateResponse(
        request=request,
        name="tag.html",
        context={"tag": tag, "entries": entries},
    )


@router.get("/entry/{entry_id}", response_class=HTMLResponse)
async def entry_page(request: Request, entry_id: UUID, db=Depends(get_db)):
    """Single entry page with thread."""
    repo = EntryRepository(db)
    entry = await repo.get_by_id(entry_id)

    if not entry:
        return templates.TemplateResponse(
            request=request,
            name="404.html",
            context={"message": "Entry not found"},
            status_code=404,
        )

    # Get parent entry if this is a response
    parent = None
    if entry.responding_to:
        parent = await repo.get_by_id(entry.responding_to)

    # Get responses to this entry
    responses = await repo.get_responses(entry_id)

    # Render content as markdown
    content_html = render_markdown(entry.content)

    return templates.TemplateResponse(
        request=request,
        name="entry.html",
        context={
            "entry": entry,
            "parent": parent,
            "responses": responses,
            "content_html": content_html,
        },
    )
