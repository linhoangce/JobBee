"""HTML helpers for Gmail OAuth popup completion."""

from __future__ import annotations

import html
import json


def oauth_popup_result_html(*, ok: bool, message: str, target_origin: str = "*") -> str:
    payload = json.dumps({"type": "jobbee:gmail-oauth", "ok": ok, "message": message})
    safe_message = html.escape(message)
    safe_origin = json.dumps(target_origin)
    title = "Gmail connected" if ok else "Gmail connect failed"
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <title>{html.escape(title)}</title>
  <style>
    body {{
      font-family: system-ui, sans-serif;
      background: #0b0d12;
      color: #e6e8ee;
      display: grid;
      place-items: center;
      min-height: 100vh;
      margin: 0;
      padding: 24px;
    }}
    .card {{
      max-width: 420px;
      padding: 24px;
      border: 1px solid #232838;
      border-radius: 12px;
      background: #11141b;
      text-align: center;
    }}
  </style>
</head>
<body>
  <div class="card">
    <h1>{html.escape(title)}</h1>
    <p>{safe_message}</p>
    <p class="muted">You can close this window.</p>
  </div>
  <script>
    (function () {{
      var payload = {payload};
      try {{
        if (window.opener && !window.opener.closed) {{
          window.opener.postMessage(payload, {safe_origin});
        }}
      }} catch (err) {{}}
      setTimeout(function () {{ window.close(); }}, 600);
    }})();
  </script>
</body>
</html>"""
