"""Custom exception types used across CodexProbe.

Keeping these in one small module lets every layer (config, transport,
proxy, recorder) raise a specific, catchable error without importing each
other's internals.
"""

from __future__ import annotations


class CodexProbeError(Exception):
    """Base class for all errors raised by CodexProbe."""


class ConfigError(CodexProbeError):
    """Raised when a recorder configuration mapping fails validation."""


class UpstreamError(CodexProbeError):
    """Raised when the configured backend cannot be reached or errors out."""
