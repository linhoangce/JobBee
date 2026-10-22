"""Job-site accounts the apply agent made for the user (Settings → Applying for you).

GET    /api/site-accounts
POST   /api/site-accounts/{id}/reveal     the password, for one explicit click
DELETE /api/site-accounts/{id}            forget it (the account on the site stays)

Reveal is the one place JobBee returns a secret: these are the user's own logins,
which they may need to check an application on the site themselves. The API
only listens on localhost.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..apply.accounts import account_password, forget
from ..db import get_session
from ..models import SiteAccount
from ..schemas import SiteAccountSecret, SiteAccountView
from .home import _aware
from .profile import get_or_create_local_user

router = APIRouter(prefix="/site-accounts", tags=["site-accounts"])


def _account(session: Session, account_id: int) -> SiteAccount:
    user = get_or_create_local_user(session)
    account = session.get(SiteAccount, account_id)
    if account is None or account.user_id != user.id:
        raise HTTPException(status_code=404, detail="Account not found.")
    return account


@router.get("", response_model=list[SiteAccountView])
def list_site_accounts(session: Session = Depends(get_session)) -> list[SiteAccountView]:
    user = get_or_create_local_user(session)
    rows = session.execute(
        select(SiteAccount)
        .where(SiteAccount.user_id == user.id)
        .order_by(SiteAccount.last_used_at.desc(), SiteAccount.id.desc())
    ).scalars()
    return [
        SiteAccountView(
            id=a.id,
            host=a.host,
            login=a.login,
            status=a.status,
            created_at=_aware(a.created_at),
            last_used_at=_aware(a.last_used_at),
        )
        for a in rows
    ]


@router.post("/{account_id}/reveal", response_model=SiteAccountSecret)
def reveal_site_account(
    account_id: int, session: Session = Depends(get_session)
) -> SiteAccountSecret:
    account = _account(session, account_id)
    password = account_password(account.user_id, account.host)
    if not password:
        raise HTTPException(status_code=404, detail="The password isn't saved on this PC any more.")
    return SiteAccountSecret(password=password)


@router.delete("/{account_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_site_account(account_id: int, session: Session = Depends(get_session)) -> None:
    forget(session, _account(session, account_id))
