#!/usr/bin/env python3
"""One-time Gmail OAuth setup for JobBee INSTALLERS (not end users).

End users only see "Sign in with Google" in the app.
Run this once, then restart the backend.

Usage:
    cd backend
    .\\.venv\\Scripts\\python.exe scripts\\setup_gmail_oauth.py
"""

from __future__ import annotations

import getpass
import re
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
ENV_FILE = BACKEND_DIR / ".env"
REDIRECT_URI = "http://127.0.0.1:8000/api/email/gmail/callback"
FRONTEND_URL = "http://localhost:5173"


def set_env_line(path: Path, key: str, value: str) -> None:
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    pattern = re.compile(rf"^\s*{re.escape(key)}\s*=")
    new_line = f"{key}={value}"
    out: list[str] = []
    found = False
    for line in lines:
        if pattern.match(line):
            out.append(new_line)
            found = True
        else:
            out.append(line)
    if not found:
        out.append(new_line)
    path.write_text("\n".join(out) + "\n", encoding="utf-8")


def main() -> int:
    print()
    print("JobBee Gmail OAuth — installer setup (one time only)")
    print("End users will never see this. They only click 'Sign in with Google'.")
    print()
    print("1. Open Google Cloud Console:")
    print("   https://console.cloud.google.com/")
    print()
    print("2. Create or select a project, then enable Gmail API:")
    print("   https://console.cloud.google.com/apis/library/gmail.googleapis.com")
    print()
    print("3. Create OAuth credentials (Web application):")
    print("   https://console.cloud.google.com/apis/credentials/oauthclient")
    print("   Authorized redirect URI (copy exactly):")
    print(f"   {REDIRECT_URI}")
    print()
    print("4. OAuth consent screen — add yourself as a test user (required while app is in Testing):")
    print("   https://console.cloud.google.com/apis/credentials/consent")
    print("   → Audience (or Test users) → Add users → add the Gmail address you'll sign in with")
    print("   While the app is unverified, ONLY those test users can connect. No Google review needed for personal use.")
    print()
    print("5. Paste your credentials below.")
    print()

    client_id = input("Client ID: ").strip()
    client_secret = getpass.getpass("Client Secret: ").strip()

    if not client_id or not client_secret:
        print("Both Client ID and Client Secret are required.", file=sys.stderr)
        return 1

    set_env_line(ENV_FILE, "GMAIL_CLIENT_ID", client_id)
    set_env_line(ENV_FILE, "GMAIL_CLIENT_SECRET", client_secret)
    set_env_line(ENV_FILE, "GMAIL_OAUTH_REDIRECT_URI", REDIRECT_URI)
    set_env_line(ENV_FILE, "FRONTEND_URL", FRONTEND_URL)

    print()
    print(f"Saved to {ENV_FILE}")
    print("Restart the backend, then users can click Sign in with Google.")
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
