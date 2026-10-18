"""Pluggable job listing sources.

Job listings are ingested from LinkedIn job-alert emails (Gmail/IMAP sync).
LinkedIn automated scraping is intentionally NOT implemented — it violates
LinkedIn's User Agreement and triggers aggressive bot detection.
"""

from .base import JobSourceError, RawJobListing
from .registry import AVAILABLE_SOURCES, EMAIL_SOURCES, get_sources

__all__ = ["AVAILABLE_SOURCES", "EMAIL_SOURCES", "JobSourceError", "RawJobListing", "get_sources"]
