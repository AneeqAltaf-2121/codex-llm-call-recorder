"""Tests for codex_probe.config: the config-only backend swap boundary."""

from __future__ import annotations

import pytest

from codex_probe.config import RecorderConfig, load_config
from codex_probe.errors import ConfigError


def _backend(**overrides):
    backend = {
        "name": "openai",
        "base_url": "https://api.openai.com/v1",
        "wire_api": "responses",
        "api_key_env": "OPENAI_API_KEY",
    }
    backend.update(overrides)
    return backend


def test_missing_backend_section_raises():
    with pytest.raises(ConfigError, match="backend"):
        load_config({"listen_port": 8135})


def test_config_must_be_a_dict():
    with pytest.raises(ConfigError, match="dict"):
        load_config(["not", "a", "dict"])  # type: ignore[arg-type]


@pytest.mark.parametrize("port", [-1, 70000, 999999])
def test_invalid_port_raises(port):
    with pytest.raises(ConfigError):
        load_config({"listen_port": port, "backend": _backend()})


def test_unsupported_wire_api_raises():
    with pytest.raises(ConfigError):
        load_config({"backend": _backend(wire_api="carrier_pigeon")})


def test_empty_backend_name_raises():
    with pytest.raises(ConfigError):
        load_config({"backend": _backend(name="  ")})


def test_backend_base_url_must_be_http():
    with pytest.raises(ConfigError):
        load_config({"backend": _backend(base_url="ftp://example.com")})


def test_missing_log_dir_is_created_automatically(tmp_path):
    log_dir = tmp_path / "does" / "not" / "exist"
    assert not log_dir.exists()

    config = load_config({"backend": _backend(), "log_dir": str(log_dir)})

    assert log_dir.exists()
    assert config.log_dir == log_dir


def test_valid_openai_configuration(tmp_path):
    config = load_config(
        {
            "listen_host": "127.0.0.1",
            "listen_port": 8135,
            "backend": _backend(),
            "log_dir": str(tmp_path / "logs"),
            "seed": 123,
        }
    )

    assert isinstance(config, RecorderConfig)
    assert config.backend.name == "openai"
    assert config.backend.wire_api == "responses"
    assert config.backend.api_key_env == "OPENAI_API_KEY"
    assert config.listen_port == 8135
    assert config.seed == 123


def test_valid_local_qwen_configuration(tmp_path):
    config = load_config(
        {
            "backend": {
                "name": "qwen-ollama",
                "base_url": "http://127.0.0.1:11434/v1",
                "wire_api": "chat_completions",
            },
            "log_dir": str(tmp_path / "logs"),
        }
    )

    assert config.backend.name == "qwen-ollama"
    assert config.backend.wire_api == "chat_completions"
    assert config.backend.api_key_env is None


def test_base_url_trailing_slash_is_normalized(tmp_path):
    config = load_config(
        {
            "backend": _backend(base_url="https://api.openai.com/v1/"),
            "log_dir": str(tmp_path / "logs"),
        }
    )

    assert config.backend.base_url == "https://api.openai.com/v1"


def test_unknown_top_level_key_rejected(tmp_path):
    with pytest.raises(ConfigError):
        load_config(
            {
                "backend": _backend(),
                "log_dir": str(tmp_path / "logs"),
                "totally_unknown_field": True,
            }
        )
