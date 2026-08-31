"""Validated configuration for :class:`~codex_probe.recorder.ProxyRecorder`.

The core research requirement of this project is that swapping the LLM
backend Codex talks to (OpenAI, a local Qwen model served by Ollama or
vLLM, or anything else that speaks an OpenAI-compatible wire protocol)
must be a *configuration* change, never a code change. This module is the
single place that turns a raw ``dict`` (as passed to ``ProxyRecorder``) into
a validated, typed object that the rest of the package relies on. No other
module should read a raw config dict directly.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field, ValidationError, field_validator

from codex_probe.errors import ConfigError

#: The two OpenAI-compatible wire protocols CodexProbe understands.
#: "responses" is the newer OpenAI Responses API (what Codex CLI defaults
#: to); "chat_completions" is the older, more broadly supported protocol
#: implemented by Ollama, vLLM, and most self-hosted OpenAI-compatible
#: servers.
WireApi = Literal["responses", "chat_completions"]


class BackendConfig(BaseModel):
    """Describes the single upstream LLM backend a recorder session talks to."""

    #: Human-readable identifier, e.g. "openai" or "qwen-ollama". Stored in
    #: logs so a researcher can tell sessions apart without inspecting URLs.
    name: str

    #: Base URL of the upstream OpenAI-compatible API, e.g.
    #: "https://api.openai.com/v1" or "http://127.0.0.1:11434/v1".
    base_url: str

    #: Which wire protocol the backend speaks. Codex CLI's Rust core picks
    #: one of these when it builds a request; the proxy does not translate
    #: between them, it simply records which one is in use.
    wire_api: WireApi

    #: Name of an environment variable holding the API key/token to inject
    #: into outgoing requests. Optional because local backends (Ollama)
    #: typically need no authentication at all.
    api_key_env: str | None = None

    model_config = {"extra": "forbid"}

    @field_validator("name")
    @classmethod
    def _non_empty_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("backend.name must not be empty")
        return value

    @field_validator("base_url")
    @classmethod
    def _normalize_base_url(cls, value: str) -> str:
        value = value.strip()
        if not (value.startswith("http://") or value.startswith("https://")):
            raise ValueError("backend.base_url must start with http:// or https://")
        return value.rstrip("/")


class RecorderConfig(BaseModel):
    """Fully validated configuration accepted by ``ProxyRecorder``."""

    #: Local interface the proxy listens on.
    listen_host: str = "127.0.0.1"

    #: Local TCP port the proxy listens on. 0 means "pick a free port",
    #: which is convenient for tests and for running several sessions
    #: concurrently.
    listen_port: int = Field(default=8135, ge=0, le=65535)

    #: The single upstream backend this session records against.
    backend: BackendConfig

    #: Directory session logs are written under. Created automatically if
    #: it does not already exist, since forcing the researcher to
    #: pre-create it before every run adds friction without adding safety.
    log_dir: Path = Path("./logs")

    #: Optional experiment seed, recorded in session metadata for
    #: reproducibility. CodexProbe does not itself use this value; it is
    #: forwarded to logs so a researcher can correlate a session with the
    #: sampling seed used elsewhere in an experiment.
    seed: int | None = None

    #: Upstream request timeout in seconds. Generous by default because
    #: local, CPU-bound model backends can be slow to produce a first token.
    request_timeout: float = Field(default=600.0, gt=0)

    model_config = {"extra": "forbid"}


def load_config(raw: dict[str, Any]) -> RecorderConfig:
    """Validate ``raw`` (as passed to ``ProxyRecorder(config)``) and return it.

    Raises:
        ConfigError: if ``raw`` is not a mapping, is missing required
            fields, references an unsupported wire API, or otherwise fails
            validation.
    """
    if not isinstance(raw, dict):
        raise ConfigError("configuration must be a dict")
    if "backend" not in raw:
        raise ConfigError("configuration is missing required 'backend' section")

    try:
        config = RecorderConfig.model_validate(raw)
    except ValidationError as exc:
        raise ConfigError(_format_validation_error(exc)) from exc

    config.log_dir.mkdir(parents=True, exist_ok=True)
    return config


def _format_validation_error(exc: ValidationError) -> str:
    """Render a pydantic ValidationError as one readable line per error."""
    parts = []
    for error in exc.errors():
        loc = ".".join(str(segment) for segment in error["loc"]) or "<root>"
        parts.append(f"{loc}: {error['msg']}")
    return "; ".join(parts)
