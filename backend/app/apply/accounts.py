"""Job-site accounts the apply agent makes and signs in with, one per host.

Each Workday company (tenant), iCIMS portal and so on wants its own account.
JobBee makes it with the applicant's email and a generated password, keeps the
password in the OS keyring (`site:<host>`) and indexes it in `site_accounts`.
Passwords never go to the model: the driver types them into the page itself,
and only while the tab is on that same host.
"""

from __future__ import annotations

import secrets
import string
from datetime import datetime, timezone

from anthropic.tools import ToolError
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import SessionLocal
from ..models import SiteAccount, User
from ..pipeline.prefs import load_preferences
from ..services.secrets import get_user_secret, set_user_secret

_SYMBOLS = "!@#$%&*"
_ALPHABET = string.ascii_letters + string.digits + _SYMBOLS


def secret_name(host: str) -> str:
    return f"site:{host.lower()}"


def generate_password(length: int = 20) -> str:
    """Random, with upper, lower, digit and symbol so any site's rules accept it."""

    required = [
        secrets.choice(string.ascii_uppercase),
        secrets.choice(string.ascii_lowercase),
        secrets.choice(string.digits),
        secrets.choice(_SYMBOLS),
    ]
    chars = required + [secrets.choice(_ALPHABET) for _ in range(length - len(required))]
    secrets.SystemRandom().shuffle(chars)
    return "".join(chars)


def get_account(session: Session, user_id: int, host: str) -> SiteAccount | None:
    return session.execute(
        select(SiteAccount).where(SiteAccount.user_id == user_id, SiteAccount.host == host.lower())
    ).scalar_one_or_none()


def account_password(user_id: int, host: str) -> str | None:
    return get_user_secret(user_id, secret_name(host)) or None


def new_password(session: Session, user_id: int, host: str, login: str, *, status: str) -> str:
    """Generate and store a password for this host (a new account or a reset)."""

    password = generate_password()
    set_user_secret(user_id, secret_name(host), password)
    account = get_account(session, user_id, host)
    if account is None:
        account = SiteAccount(user_id=user_id, host=host.lower(), login=login)
        session.add(account)
    account.login = login
    account.status = status
    account.last_used_at = datetime.now(timezone.utc)
    session.commit()
    return password


def mark(session: Session, user_id: int, host: str, *, status: str | None = None) -> None:
    account = get_account(session, user_id, host)
    if account is None:
        return
    if status:
        account.status = status
    account.last_used_at = datetime.now(timezone.utc)
    session.commit()


def credentials(user_id: int, host: str, action: str) -> tuple[str, str, str]:
    """(login, password, event kind) for the agent's `site_account` call on `host`.

    "create" and "set_new_password" make and store a new password first. "rejected"
    records that the site refused the saved password (no password returned): it isn't
    tried again until it's reset, since repeated failures lock accounts. Raises
    ToolError, worded for the agent, when it isn't allowed or there's no account.
    """

    with SessionLocal() as session:
        user = session.get(User, user_id)
        if not load_preferences(user).agent_accounts:
            raise ToolError(
                "The applicant hasn't allowed JobBee to create or use job-site accounts. "
                "Call `finish` with status 'needs_you' and say the site needs an account."
            )
        if not user.email:
            raise ToolError(
                "The applicant's profile has no email address. Call `finish` with status "
                "'needs_you' and say an email is needed in their JobBee profile."
            )
        account = get_account(session, user_id, host)
        if action == "rejected":
            mark(session, user_id, host, status="rejected")
            return (account.login if account else user.email), "", "account_rejected"
        if action == "sign_in":
            password = account_password(user_id, host) if account else None
            if account is None or not password:
                raise ToolError(
                    f"JobBee has no saved account on {host}. Create one (action 'create'). "
                    f"If the site says {user.email} is already registered, use its "
                    "'Forgot password' flow."
                )
            if account.status == "rejected":
                raise ToolError(
                    f"{host} refused JobBee's saved password before, and another try could "
                    "lock the account. Use the site's 'Forgot password' link, then "
                    "`check_inbox` (purpose 'reset_password'), then `site_account` "
                    "(action 'set_new_password')."
                )
            mark(session, user_id, host)
            return account.login, password, "account_signed_in"
        if action == "create":
            return (
                user.email,
                new_password(session, user_id, host, user.email, status="created"),
                "account_created",
            )
        login = account.login if account else user.email
        return login, new_password(session, user_id, host, login, status="reset"), "password_reset"


def forget(session: Session, account: SiteAccount) -> None:
    set_user_secret(account.user_id, secret_name(account.host), "")
    session.delete(account)
    session.commit()
