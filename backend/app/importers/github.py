"""Pull profile data from GitHub's public REST API.

We avoid an LLM here on purpose -- GitHub's user and repo JSON is already
structured, and deriving skills from `languages_url` is deterministic.
This keeps the importer fast and free.

If the user supplies a personal access token we attach it for higher rate
limits and access to private-repo language stats.
"""

from __future__ import annotations

from collections import Counter
from typing import Any

import httpx

from .base import Draft, ImporterError

GITHUB_API = "https://api.github.com"
USER_AGENT = "JobBee/0.1 (+local-agent)"

# GitHub "topic" tags we recognize as concrete skills. Topic naming is a
# free-form mess otherwise.
_TOPIC_SKILLS = {
    "python", "typescript", "javascript", "rust", "go", "golang", "java",
    "kotlin", "swift", "ruby", "php", "c", "cpp", "csharp", "scala", "elixir",
    "haskell", "react", "nextjs", "vue", "svelte", "angular", "nodejs",
    "django", "flask", "fastapi", "rails", "spring", "express", "graphql",
    "rest", "postgres", "postgresql", "mysql", "sqlite", "redis", "mongodb",
    "kafka", "rabbitmq", "elasticsearch", "kubernetes", "k8s", "docker",
    "terraform", "ansible", "aws", "gcp", "azure", "linux", "ml", "llm",
    "ai", "pytorch", "tensorflow", "playwright", "selenium", "wasm",
}

# Languages we never want to surface as a "skill" because they're config /
# tooling-only. The GitHub language stats include these.
_LANGUAGE_BLOCKLIST = {
    "shell", "dockerfile", "makefile", "html", "css", "scss", "vim script",
    "batchfile", "powershell", "nix", "cmake", "smarty", "jinja",
}


def _normalize_lang(name: str) -> str:
    lower = name.lower().strip()
    if lower in {"javascript", "js"}:
        return "javascript"
    if lower in {"typescript", "ts"}:
        return "typescript"
    if lower in {"c++", "cpp"}:
        return "cpp"
    if lower in {"c#", "csharp"}:
        return "csharp"
    return lower


async def _get_json(
    client: httpx.AsyncClient, url: str, token: str | None
) -> Any:
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": USER_AGENT,
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    resp = await client.get(url, headers=headers, follow_redirects=True)
    if resp.status_code == 404:
        raise ImporterError(f"GitHub user not found at {url}.")
    if resp.status_code == 403:
        raise ImporterError(
            "GitHub rate-limited the request. Add a personal access token in"
            " the import form (or wait an hour and try again)."
        )
    if resp.status_code >= 400:
        raise ImporterError(
            f"GitHub returned {resp.status_code} for {url}: {resp.text[:200]}"
        )
    return resp.json()


async def import_github(username: str, *, token: str | None = None) -> Draft:
    """Build a profile draft from a GitHub username.

    Sources used:
      - `GET /users/{u}` for bio, blog, location, name, hireable
      - `GET /users/{u}/repos?per_page=100` for top languages, topics, and
        a short summary of the user's most-starred recent work

    Returns an empty `Draft` (with `source="github"`) if the user has no
    public footprint at all.
    """

    username = username.strip().lstrip("@")
    if not username:
        raise ImporterError("GitHub username is empty.")

    async with httpx.AsyncClient(timeout=15.0) as client:
        user = await _get_json(client, f"{GITHUB_API}/users/{username}", token)
        repos: list[dict[str, Any]] = await _get_json(
            client,
            f"{GITHUB_API}/users/{username}/repos?per_page=100&type=owner&sort=updated",
            token,
        )

    draft = Draft(source="github")

    name = (user.get("name") or "").strip()
    draft.set("name", name)

    bio = (user.get("bio") or "").strip()
    if bio:
        # Use bio as headline if it's short, otherwise as summary.
        if len(bio) <= 120:
            draft.set("headline", bio)
        else:
            draft.set("summary", bio)

    location = (user.get("location") or "").strip()
    draft.set("location", location)

    links: dict[str, str] = {"github": user.get("html_url", f"https://github.com/{username}")}
    blog = (user.get("blog") or "").strip()
    if blog:
        url = blog if blog.startswith(("http://", "https://")) else f"https://{blog}"
        links["portfolio"] = url
    draft.set("links", links)

    # Skills from repos: weighted by stars + language bytes when available.
    skill_score: Counter[str] = Counter()
    for r in repos:
        if r.get("fork"):
            continue
        stars = int(r.get("stargazers_count") or 0)
        weight = 1 + stars

        lang = r.get("language")
        if lang:
            normalized = _normalize_lang(lang)
            if normalized not in _LANGUAGE_BLOCKLIST:
                skill_score[normalized] += weight

        for topic in r.get("topics") or []:
            tnorm = _normalize_lang(topic)
            if tnorm in _TOPIC_SKILLS:
                skill_score[tnorm] += weight

    top_skills = [s for s, _ in skill_score.most_common(12)]
    draft.set("skills", top_skills)

    # If we never got a summary from bio, build a short auto-summary from the
    # top non-fork repos so the review UI has *something* to show.
    if "summary" not in draft.data:
        top_repos = sorted(
            (r for r in repos if not r.get("fork") and (r.get("description") or "").strip()),
            key=lambda r: int(r.get("stargazers_count") or 0),
            reverse=True,
        )[:3]
        if top_repos:
            descs = [
                f"{r['name']}: {r['description'].strip()}"
                for r in top_repos
                if r.get("description")
            ]
            draft.set("summary", " | ".join(descs))

    return draft
