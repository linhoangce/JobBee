"""Company watchlist: job boards the agent checks on every search.

GET    /api/watchlist
POST   /api/watchlist          {company: name or careers URL}
DELETE /api/watchlist/{id}
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_session
from ..models import WatchlistCompany
from ..pipeline.sources.ats_boards import find_board
from ..pipeline.sources.base import SourceError
from ..schemas import WatchlistAddRequest, WatchlistItem
from .profile import get_or_create_local_user

router = APIRouter(prefix="/watchlist", tags=["watchlist"])

_MAX_COMPANIES = 50


@router.get("", response_model=list[WatchlistItem])
def list_watchlist(session: Session = Depends(get_session)) -> list[WatchlistItem]:
    user = get_or_create_local_user(session)
    rows = session.execute(
        select(WatchlistCompany)
        .where(WatchlistCompany.user_id == user.id)
        .order_by(WatchlistCompany.name)
    ).scalars()
    return [WatchlistItem.model_validate(r) for r in rows]


@router.post("", response_model=WatchlistItem, status_code=status.HTTP_201_CREATED)
async def add_company(
    payload: WatchlistAddRequest, session: Session = Depends(get_session)
) -> WatchlistItem:
    user = get_or_create_local_user(session)
    count = len(
        session.execute(
            select(WatchlistCompany.id).where(WatchlistCompany.user_id == user.id)
        ).all()
    )
    if count >= _MAX_COMPANIES:
        raise HTTPException(
            status_code=400, detail=f"The watchlist holds {_MAX_COMPANIES} companies."
        )
    text = payload.company.strip()
    try:
        board = await find_board(text)
    except SourceError as e:
        raise HTTPException(status_code=502, detail=str(e)) from e
    if board is None:
        raise HTTPException(
            status_code=404,
            detail=(
                f"Couldn't find a Greenhouse, Lever or Ashby job board for “{text}”. "
                "Paste the company's careers page link instead."
            ),
        )
    existing = session.execute(
        select(WatchlistCompany).where(
            WatchlistCompany.user_id == user.id,
            WatchlistCompany.ats == board.ats,
            WatchlistCompany.slug == board.slug,
        )
    ).scalar_one_or_none()
    if existing is not None:
        return WatchlistItem.model_validate(existing)
    row = WatchlistCompany(
        user_id=user.id,
        name=board.name[:255],
        ats=board.ats,
        slug=board.slug,
        careers_url=text if "/" in text else None,
    )
    session.add(row)
    session.commit()
    session.refresh(row)
    return WatchlistItem.model_validate(row)


@router.delete("/{item_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_company(item_id: int, session: Session = Depends(get_session)) -> None:
    user = get_or_create_local_user(session)
    row = session.get(WatchlistCompany, item_id)
    if row is None or row.user_id != user.id:
        raise HTTPException(status_code=404, detail="Not on your watchlist.")
    session.delete(row)
    session.commit()
