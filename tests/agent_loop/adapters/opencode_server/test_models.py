"""Unit tests for documented OpenCode Server payload parsing."""

from __future__ import annotations

import pytest

from agent_loop.adapters.opencode_server.errors import OpenCodeServerError
from agent_loop.adapters.opencode_server.errors import ServerFailureCode
from agent_loop.adapters.opencode_server.models import ServerInfo
from agent_loop.adapters.opencode_server.models import parse_agent_inventory
from agent_loop.adapters.opencode_server.models import parse_diffs
from agent_loop.adapters.opencode_server.models import parse_messages
from agent_loop.adapters.opencode_server.models import parse_model_inventory
from agent_loop.adapters.opencode_server.models import parse_session_envelope

_INPUT_TOKENS = 10
_REASONING_TOKENS = 2
_ADDITIONS = 2


def test_server_info_parses_observed_payload() -> None:
    """Observed v2.2.6 payload fields are retained."""
    info = ServerInfo.from_payload(
        {
            "version": "2.2.6",
            "pid": 123,
            "urls": ["http://127.0.0.1:4096"],
            "paths": {"tmp": "/workspace/tmp"},
        },
    )
    assert info == ServerInfo("2.2.6", 123, ("http://127.0.0.1:4096",))


def test_server_info_missing_version_stays_unknown() -> None:
    """A malformed/missing version is not synthesized."""
    info = ServerInfo.from_payload({"pid": 1, "urls": []})
    assert info.version is None


def test_model_inventory_parses_and_deduplicates() -> None:
    """Model refs use documented provider/model identity without duplicates."""
    models = parse_model_inventory(
        {
            "data": [
                {"providerID": "opencode-go", "id": "model-a"},
                {"providerID": "opencode-go", "id": "model-a"},
                {"providerID": "ollama", "id": "qwen3-coder:30b"},
            ],
        },
    )
    assert [model.as_text() for model in models] == ["opencode-go/model-a", "ollama/qwen3-coder:30b"]


def test_model_inventory_rejects_malformed_entries() -> None:
    """A partial inventory is not silently accepted."""
    with pytest.raises(OpenCodeServerError) as captured:
        parse_model_inventory({"data": [{"providerID": "x"}]})
    assert captured.value.code is ServerFailureCode.INVALID_RESPONSE


def test_agent_inventory_parses_ids() -> None:
    """Agent ids are read from the documented envelope."""
    assert parse_agent_inventory({"data": [{"id": "build"}, {"id": "plan"}]}) == ("build", "plan")


def test_session_envelope_parses_required_fields() -> None:
    """Session identity, location, model, outcome, and usage are retained."""
    session = parse_session_envelope(
        {
            "data": {
                "id": "ses_123",
                "location": {"directory": "/workspace"},
                "model": {"providerID": "p", "id": "m", "variant": "default"},
                "outcome": "succeeded",
                "tokens": {"input": 10, "output": 5, "reasoning": 2, "cache": {"read": 1, "write": 0}},
                "cost": 0.5,
                "title": "t",
            },
        },
    )
    assert session.session_id == "ses_123"
    assert session.directory == "/workspace"
    assert session.model is not None
    assert session.model.as_text() == "p/m#default"
    assert session.tokens.input_tokens == _INPUT_TOKENS
    assert session.tokens.reasoning_tokens == _REASONING_TOKENS


def test_messages_extract_assistant_text_and_tool_failures() -> None:
    """Only assistant text/tool errors become normalized observations."""
    messages = parse_messages(
        {
            "data": [
                {"type": "user", "id": "msg_u", "text": "ignored"},
                {
                    "type": "assistant",
                    "id": "msg_a",
                    "content": [
                        {"type": "reasoning", "text": "private"},
                        {"type": "text", "text": "done"},
                        {
                            "type": "tool",
                            "name": "bash",
                            "state": {"status": "error", "error": {"message": "boom"}},
                        },
                    ],
                },
            ],
        },
    )
    assert messages[0].message_id == "msg_a"
    assert messages[0].text == "done"
    assert messages[0].tool_failures == ("bash:boom",)


def test_diffs_parse_file_metadata() -> None:
    """Diff evidence keeps file/status/counts and ignores the raw patch."""
    diffs = parse_diffs(
        {
            "data": [
                {
                    "file": "a.py",
                    "patch": "diff...",
                    "additions": 2,
                    "deletions": 1,
                    "status": "modified",
                },
            ],
        },
    )
    assert diffs[0].file == "a.py"
    assert diffs[0].additions == _ADDITIONS


def test_invalid_envelopes_fail_closed() -> None:
    """Malformed envelopes raise invalid_response, never an empty success."""
    with pytest.raises(OpenCodeServerError) as captured:
        parse_messages({"data": "not-a-list"})
    assert captured.value.code is ServerFailureCode.INVALID_RESPONSE
