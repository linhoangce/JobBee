"""Thin inbox clients for Gmail (REST + OAuth) and IMAP."""

from __future__ import annotations

import base64
import email
import imaplib
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from email.header import decode_header, make_header
from email.utils import parsedate_to_datetime
from typing import Protocol

import httpx

from ..config import Settings
from ..models import EmailSyncState
from .email_connection import UserEmailConnection

GMAIL_API_BASE = "https://gmail.googleapis.com/gmail/v1"
OAUTH_TOKEN_URL = "https://oauth2.googleapis.com/token"
DEFAULT_MAX_MESSAGES = 50
SNIPPET_MAX_LEN = 200
# Job-alert senders only — excludes LinkedIn DMs, surveys, customer service, etc.
GMAIL_JOB_ALERT_QUERY = (
    "newer_than:14d ("
    "from:jobs-listings@linkedin.com OR "
    "from:jobalerts-noreply@linkedin.com OR "
    "from:jobs@linkedin.com OR "
    "from:alert@indeed.com OR "
    'subject:"jobs matching" OR '
    'subject:"new jobs for" OR '
    'subject:"job alert"'
    ")"
)


class EmailClientError(Exception):
    """Base error for inbox connectivity or API failures."""


class EmailNotConfiguredError(EmailClientError):
    """Raised when the provider is set but required credentials are missing."""


@dataclass
class RawEmailMessage:
    provider_message_id: str
    subject: str
    sender: str
    received_at: datetime | None
    snippet: str
    body: str = ""


class EmailClient(Protocol):
    def ping(self) -> None:
        """Verify credentials and connectivity."""

    def fetch_new_messages(
        self,
        *,
        cursor: EmailSyncState,
        max_messages: int = DEFAULT_MAX_MESSAGES,
    ) -> tuple[list[RawEmailMessage], dict[str, object]]:
        """Return new messages and cursor field updates for the caller to persist."""

    def search_recent(
        self, *, senders: list[str], since: datetime, max_messages: int = 10
    ) -> list[RawEmailMessage]:
        """Messages from any of these sender domains received after `since`, newest first.

        Leaves the job-alert cursor alone.
        """


def build_email_client(
    connection: UserEmailConnection,
    *,
    app_settings: Settings,
    gmail_client_id: str | None = None,
    gmail_client_secret: str | None = None,
) -> EmailClient:
    """Instantiate an inbox client from the user's saved connection."""

    if connection.provider == "gmail":
        client_id = (gmail_client_id or app_settings.gmail_client_id).strip()
        client_secret = (gmail_client_secret or app_settings.gmail_client_secret).strip()
        if not client_id or not client_secret:
            raise EmailNotConfiguredError(
                "Gmail connect is not set up yet. Complete the one-time Google setup in Settings."
            )
        if not (connection.gmail_refresh_token or "").strip():
            raise EmailNotConfiguredError("Gmail is not connected for this user.")
        return GmailClient(
            client_id=client_id,
            client_secret=client_secret,
            refresh_token=connection.gmail_refresh_token or "",
        )
    if connection.provider == "imap":
        return ImapClient(connection)
    raise EmailClientError(f"Unsupported email provider: {connection.provider}")


def _decode_header_value(value: str | None) -> str:
    if not value:
        return ""
    try:
        return str(make_header(decode_header(value)))
    except (email.errors.HeaderParseError, UnicodeError, TypeError, ValueError):
        return value


def _parse_email_date(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = parsedate_to_datetime(value)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (TypeError, ValueError, IndexError):
        return None


def _truncate_snippet(text: str, limit: int = SNIPPET_MAX_LEN) -> str:
    cleaned = " ".join(text.split())
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: limit - 3] + "..."


def _extract_snippet_from_message(msg: email.message.Message) -> str:
    return _truncate_snippet(_extract_body_from_message(msg))


def _decode_part_payload(part: email.message.Message) -> str:
    payload = part.get_payload(decode=True)
    if not payload:
        return ""
    charset = part.get_content_charset() or "utf-8"
    try:
        return payload.decode(charset, errors="replace")
    except LookupError:
        return payload.decode("utf-8", errors="replace")


def _extract_body_from_message(msg: email.message.Message) -> str:
    """Prefer HTML (better for LinkedIn alerts), fall back to plain text."""

    html = ""
    plain = ""
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_maintype() == "multipart":
                continue
            content_type = part.get_content_type()
            if content_type == "text/html" and not html:
                html = _decode_part_payload(part)
            elif content_type == "text/plain" and not plain:
                plain = _decode_part_payload(part)
    else:
        text = _decode_part_payload(msg)
        if msg.get_content_type() == "text/html":
            html = text
        else:
            plain = text
    return html or plain


def _decode_gmail_body_data(data: str) -> str:
    padded = data + "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(padded).decode("utf-8", errors="replace")


def _extract_body_from_gmail_payload(payload: dict) -> str:
    html_parts: list[str] = []
    plain_parts: list[str] = []

    def walk(part: dict) -> None:
        mime = part.get("mimeType") or ""
        body = part.get("body") or {}
        data = body.get("data")
        if data:
            text = _decode_gmail_body_data(data)
            if mime == "text/html":
                html_parts.append(text)
            elif mime == "text/plain":
                plain_parts.append(text)
        for sub in part.get("parts") or []:
            walk(sub)

    walk(payload)
    if html_parts:
        return html_parts[0]
    if plain_parts:
        return plain_parts[0]
    return ""


class GmailClient:
    """Gmail REST client using OAuth refresh token + httpx."""

    def __init__(
        self,
        *,
        client_id: str,
        client_secret: str,
        refresh_token: str,
    ) -> None:
        self._client_id = client_id.strip()
        self._client_secret = client_secret.strip()
        self._refresh_token = refresh_token.strip()
        self._http = httpx.Client(timeout=30.0)
        self._access_token: str | None = None

    def _refresh_access_token(self) -> str:
        resp = self._http.post(
            OAUTH_TOKEN_URL,
            data={
                "client_id": self._client_id,
                "client_secret": self._client_secret,
                "refresh_token": self._refresh_token,
                "grant_type": "refresh_token",
            },
        )
        if resp.status_code != 200:
            raise EmailClientError(f"Gmail token refresh failed: {resp.status_code} {resp.text}")
        data = resp.json()
        token = data.get("access_token")
        if not token:
            raise EmailClientError("Gmail token refresh returned no access_token")
        self._access_token = token
        return token

    def _auth_headers(self) -> dict[str, str]:
        if not self._access_token:
            self._refresh_access_token()
        assert self._access_token
        return {"Authorization": f"Bearer {self._access_token}"}

    def _request(self, method: str, path: str, *, params: dict | None = None) -> dict:
        url = f"{GMAIL_API_BASE}{path}"
        resp = self._http.request(method, url, headers=self._auth_headers(), params=params)
        if resp.status_code == 401:
            self._access_token = None
            resp = self._http.request(method, url, headers=self._auth_headers(), params=params)
        if resp.status_code >= 400:
            raise EmailClientError(f"Gmail API {path} failed: {resp.status_code} {resp.text}")
        if resp.status_code == 204 or not resp.content:
            return {}
        return resp.json()

    def ping(self) -> None:
        self._request("GET", "/users/me/profile")

    def _profile_history_id(self) -> str:
        profile = self._request("GET", "/users/me/profile")
        history_id = profile.get("historyId")
        if not history_id:
            raise EmailClientError("Gmail profile missing historyId")
        return str(history_id)

    def _list_job_alert_message_ids(self, *, max_messages: int, unread_only: bool = False) -> list[str]:
        query = GMAIL_JOB_ALERT_QUERY
        if unread_only:
            query = f"is:unread {query}"
        data = self._request(
            "GET",
            "/users/me/messages",
            params={"q": query, "maxResults": max_messages},
        )
        return [m["id"] for m in data.get("messages", []) if m.get("id")]

    def _history_message_ids(self, start_history_id: str, *, max_messages: int) -> list[str]:
        ids: list[str] = []
        page_token: str | None = None
        while len(ids) < max_messages:
            params: dict[str, str | int] = {
                "startHistoryId": start_history_id,
                "historyTypes": "messageAdded",
                "maxResults": min(max_messages - len(ids), 100),
            }
            if page_token:
                params["pageToken"] = page_token
            try:
                data = self._request("GET", "/users/me/history", params=params)
            except EmailClientError as e:
                if "404" in str(e):
                    return []
                raise
            for record in data.get("history", []):
                for added in record.get("messagesAdded", []):
                    msg = added.get("message", {})
                    msg_id = msg.get("id")
                    if msg_id and msg_id not in ids:
                        ids.append(msg_id)
            page_token = data.get("nextPageToken")
            if not page_token:
                break
        return ids[:max_messages]

    def _fetch_message(self, message_id: str) -> RawEmailMessage:
        data = self._request(
            "GET",
            f"/users/me/messages/{message_id}",
            params={"format": "full"},
        )
        payload = data.get("payload") or {}
        headers = {h["name"].lower(): h["value"] for h in payload.get("headers", [])}
        snippet = data.get("snippet") or ""
        body = _extract_body_from_gmail_payload(payload)
        return RawEmailMessage(
            provider_message_id=message_id,
            subject=_decode_header_value(headers.get("subject")),
            sender=_decode_header_value(headers.get("from")),
            received_at=_parse_email_date(headers.get("date")),
            snippet=_truncate_snippet(snippet or body),
            body=body,
        )

    def search_recent(
        self, *, senders: list[str], since: datetime, max_messages: int = 10
    ) -> list[RawEmailMessage]:
        query = f"after:{int(since.timestamp())} from:({' OR '.join(senders)})"
        data = self._request(
            "GET", "/users/me/messages", params={"q": query, "maxResults": max_messages}
        )
        return [self._fetch_message(m["id"]) for m in data.get("messages", []) if m.get("id")]

    def fetch_new_messages(
        self,
        *,
        cursor: EmailSyncState,
        max_messages: int = DEFAULT_MAX_MESSAGES,
    ) -> tuple[list[RawEmailMessage], dict[str, object]]:
        updates: dict[str, object] = {"provider": "gmail"}
        message_ids: list[str] = []

        if cursor.gmail_history_id:
            message_ids = self._history_message_ids(cursor.gmail_history_id, max_messages=max_messages)
            if not message_ids:
                message_ids = self._list_job_alert_message_ids(max_messages=max_messages)
        else:
            message_ids = self._list_job_alert_message_ids(max_messages=max_messages)

        messages = [self._fetch_message(mid) for mid in message_ids]
        updates["gmail_history_id"] = self._profile_history_id()
        return messages, updates


class ImapClient:
    """IMAP client using stdlib imaplib."""

    def __init__(self, connection: UserEmailConnection) -> None:
        self._host = (connection.imap_host or "").strip()
        self._user = (connection.imap_user or "").strip()
        self._password = (connection.imap_app_password or "").strip()
        self._folder = connection.imap_folder.strip() or "INBOX"

    def _connect(self) -> imaplib.IMAP4_SSL:
        try:
            conn = imaplib.IMAP4_SSL(self._host)
            conn.login(self._user, self._password)
            return conn
        except imaplib.IMAP4.error as e:
            raise EmailClientError(f"IMAP login failed: {e}") from e
        except OSError as e:
            raise EmailClientError(f"IMAP connection failed: {e}") from e

    def ping(self) -> None:
        conn = self._connect()
        try:
            conn.noop()
        finally:
            try:
                conn.logout()
            except imaplib.IMAP4.error:
                pass

    def search_recent(
        self, *, senders: list[str], since: datetime, max_messages: int = 10
    ) -> list[RawEmailMessage]:
        # IMAP SINCE is a whole day; the exact cut-off is applied after fetching.
        day = since.astimezone(timezone.utc).strftime("%d-%b-%Y")
        criteria = f'FROM "{senders[0]}"'
        for sender in senders[1:]:
            criteria = f'OR FROM "{sender}" {criteria}'
        conn = self._connect()
        messages: list[RawEmailMessage] = []
        try:
            status, _ = conn.select(self._folder, readonly=True)
            if status != "OK":
                raise EmailClientError(f"IMAP SELECT {self._folder} failed: {status}")
            status, data = conn.uid("search", None, f"SINCE {day} {criteria}")
            if status != "OK":
                raise EmailClientError(f"IMAP UID SEARCH failed: {status}")
            uids = sorted(int(u) for u in (data[0].split() if data and data[0] else []))
            for uid in reversed(uids[-max_messages:]):
                status, fetch_data = conn.uid("fetch", str(uid), "(RFC822)")
                if status != "OK" or not fetch_data or fetch_data[0] is None:
                    continue
                raw_bytes = fetch_data[0][1]
                if not isinstance(raw_bytes, bytes):
                    continue
                msg = email.message_from_bytes(raw_bytes)
                received = _parse_email_date(msg.get("Date"))
                if received is not None and received < since:
                    continue
                body = _extract_body_from_message(msg)
                messages.append(
                    RawEmailMessage(
                        provider_message_id=str(uid),
                        subject=_decode_header_value(msg.get("Subject")),
                        sender=_decode_header_value(msg.get("From")),
                        received_at=received,
                        snippet=_truncate_snippet(body),
                        body=body,
                    )
                )
        finally:
            try:
                conn.logout()
            except imaplib.IMAP4.error:
                pass
        return messages

    def fetch_new_messages(
        self,
        *,
        cursor: EmailSyncState,
        max_messages: int = DEFAULT_MAX_MESSAGES,
    ) -> tuple[list[RawEmailMessage], dict[str, object]]:
        updates: dict[str, object] = {"provider": "imap"}
        conn = self._connect()
        messages: list[RawEmailMessage] = []
        try:
            status, _ = conn.select(self._folder, readonly=True)
            if status != "OK":
                raise EmailClientError(f"IMAP SELECT {self._folder} failed: {status}")

            uid_validity: int | None = None
            status_typ, status_data = conn.status(self._folder, "(UIDVALIDITY)")
            if status_typ == "OK" and status_data:
                match = re.search(r"UIDVALIDITY\s+(\d+)", status_data[0].decode())
                if match:
                    uid_validity = int(match.group(1))

            last_uid = cursor.imap_last_uid
            if (
                cursor.imap_uid_validity is not None
                and uid_validity is not None
                and cursor.imap_uid_validity != uid_validity
            ):
                last_uid = None

            updates["imap_uid_validity"] = uid_validity

            if last_uid:
                status, data = conn.uid("search", None, f"UNSEEN UID {last_uid + 1}:*")
            else:
                status, data = conn.uid("search", None, "UNSEEN")

            if status != "OK":
                raise EmailClientError(f"IMAP UID SEARCH failed: {status}")

            uid_bytes = data[0].split() if data and data[0] else []
            uids = [int(u.decode()) for u in uid_bytes if u.isdigit()]
            uids.sort()
            uids = uids[:max_messages]

            max_fetched_uid = last_uid or 0
            for uid in uids:
                status, fetch_data = conn.uid("fetch", str(uid), "(RFC822)")
                if status != "OK" or not fetch_data or fetch_data[0] is None:
                    continue
                raw_bytes = fetch_data[0][1]
                if not isinstance(raw_bytes, bytes):
                    continue
                msg = email.message_from_bytes(raw_bytes)
                body = _extract_body_from_message(msg)
                messages.append(
                    RawEmailMessage(
                        provider_message_id=str(uid),
                        subject=_decode_header_value(msg.get("Subject")),
                        sender=_decode_header_value(msg.get("From")),
                        received_at=_parse_email_date(msg.get("Date")),
                        snippet=_truncate_snippet(body),
                        body=body,
                    )
                )
                max_fetched_uid = max(max_fetched_uid, uid)

            if max_fetched_uid > (last_uid or 0):
                updates["imap_last_uid"] = max_fetched_uid
            elif last_uid is not None:
                updates["imap_last_uid"] = last_uid

        finally:
            try:
                conn.logout()
            except imaplib.IMAP4.error:
                pass

        return messages, updates
