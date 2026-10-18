"""Deterministic parsers for job-alert emails (LinkedIn, Indeed, etc.)."""

from .base import (
    EmailParser,
    ParsedJobAlert,
    extract_sender_address,
    get_parser_for_sender,
    parse_email_body,
)
from .linkedin import LinkedInEmailParser

__all__ = [
    "EmailParser",
    "LinkedInEmailParser",
    "ParsedJobAlert",
    "extract_sender_address",
    "get_parser_for_sender",
    "parse_email_body",
]
