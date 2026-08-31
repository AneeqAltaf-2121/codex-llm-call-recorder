"""CodexProbe: transparent LLM call recorder and backend swap harness.

    from codex_probe import ProxyRecorder

    recorder = ProxyRecorder(config)
    endpoint = recorder.start()
    ...
    calls = recorder.stop()
"""

from codex_probe.recorder import ProxyRecorder

__all__ = ["ProxyRecorder"]

__version__ = "0.1.0"
