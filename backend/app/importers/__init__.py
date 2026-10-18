"""Profile importers.

Each importer takes some user-supplied pointer (a resume file, a GitHub
username, eventually a URL) and produces a `Draft` -- a partial profile plus
a per-field source label. `merge.merge_drafts()` combines drafts with a
priority order and returns one normalized draft for the UI to review.

Importers never persist to the DB; the `/api/profile/import` endpoint
returns the draft so the user can edit before committing via
`PUT /api/profile`.
"""

from .base import Draft, ImporterError
from .github import import_github
from .merge import merge_drafts
from .resume import import_resume

__all__ = [
    "Draft",
    "ImporterError",
    "import_github",
    "import_resume",
    "merge_drafts",
]
