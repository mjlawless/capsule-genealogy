"""The page-reading model and its reply ceiling (2026-10-22 model test)."""
from __future__ import annotations


def test_the_reply_ceiling_stays_under_the_sdks_non_streaming_limit():
    """The SDK refuses a non-streaming request it estimates could take over 10 minutes
    (`max_tokens` above ~21,333), and the transport does not stream: every reading would
    fail with "Streaming is required". Raise past this only together with streaming."""
    from capsule.agent.base import DEFAULT_MAX_TOKENS

    assert 60 * 60 * DEFAULT_MAX_TOKENS / 128_000 <= 600
    assert DEFAULT_MAX_TOKENS >= 16_000  # a 30-result search page needed 14,008


def test_pages_are_read_by_the_model_the_comparison_chose():
    from capsule.agent.base import DEFAULT_MODEL

    assert DEFAULT_MODEL == "claude-opus-5-5"
