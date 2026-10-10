import io
import json
import urllib.error
from dataclasses import replace
from http.client import IncompleteRead

import pytest

from resource_auditor.config import RoleConfig, Settings
from resource_auditor.llm import ModelClient, ModelError, NoRedirect
from resource_auditor.models import Critique


def client():
    return ModelClient(
        Settings(
            RoleConfig("https://a.test/v1", "a", "PRIVATE_TEST_KEY"),
            RoleConfig("https://b.test/v1", "b", "test"),
        )
    )


def test_http_error_body_and_key_never_escape_to_caller(monkeypatch):
    instance = client()

    def fail(*a, **k):
        raise urllib.error.HTTPError(
            "https://a.test/v1", 401, "PRIVATE_TEST_KEY", {}, io.BytesIO(b"secret provider body")
        )

    monkeypatch.setattr(instance.opener, "open", fail)
    with pytest.raises(ModelError, match="^provider_http_401$"):
        instance.complete("extractor", "test", {}, Critique)
    assert NoRedirect().redirect_request(None, None, 302, "", {}, "https://another.test") is None


@pytest.mark.parametrize(
    "finish,content,error",
    [
        ("length", "{}", "provider_response_incomplete"),
        ("stop", "not-json", "provider_invalid_json_or_schema"),
        (
            "stop",
            '{"score":11,"approved":true,"complete":true,"issues":[]}',
            "provider_invalid_json_or_schema",
        ),
    ],
)
def test_incomplete_or_invalid_provider_response_cannot_be_success(
    monkeypatch, finish, content, error
):
    instance = client()
    raw = json.dumps(
        {"choices": [{"finish_reason": finish, "message": {"content": content}}]}
    ).encode()
    monkeypatch.setattr(instance.opener, "open", lambda *a, **k: io.BytesIO(raw))
    with pytest.raises(ModelError, match=error):
        instance.complete("critic", "test", {}, Critique)


class Stream(io.BytesIO):
    headers = {"Content-Type": "text/event-stream"}


def test_streamed_content_is_assembled_without_reasoning_or_usage(monkeypatch):
    instance = client()
    chunks = [
        {"choices": [{"index": 0, "delta": {"reasoning_content": "not JSON"}}]},
        {"choices": [{"index": 0, "delta": {"content": '{"score":9,'}}]},
        {
            "choices": [
                {"index": 0, "delta": {"content": '"approved":true,"complete":true,"issues":[]}'}}
            ]
        },
        {"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]},
        {"choices": [], "usage": {"total_tokens": 10}},
    ]
    raw = "".join("data: " + json.dumps(c) + "\n\n" for c in chunks) + "data: [DONE]\n\n"
    requests = []

    def respond(request, **kwargs):
        requests.append(request)
        return Stream(raw.encode())

    monkeypatch.setattr(instance.opener, "open", respond)
    assert instance.complete("critic", "test", {}, Critique).score == 9
    assert instance.complete("critic", "test", {}, Critique).approved
    assert requests[0].get_header("X-opencode-session") == requests[1].get_header(
        "X-opencode-session"
    )
    assert requests[0].get_header("User-agent") == "ni43-resource-auditor/0.1"


def test_stream_ending_without_completion_is_not_accepted(monkeypatch):
    instance = client()
    raw = b'data: {"choices":[{"delta":{"content":"{}"}}]}\n\ndata: [DONE]\n\n'
    monkeypatch.setattr(instance.opener, "open", lambda *a, **k: Stream(raw))
    with pytest.raises(ModelError, match="provider_response_incomplete"):
        instance.complete("critic", "test", {}, Critique)


@pytest.mark.parametrize("event", [[], {"choices": [None]}, {"choices": [{"delta": None}]}])
def test_malformed_stream_shapes_become_sanitized_model_errors(monkeypatch, event):
    instance = client()
    raw = ("data: " + json.dumps(event) + "\n\n").encode()
    monkeypatch.setattr(instance.opener, "open", lambda *a, **k: Stream(raw))
    with pytest.raises(ModelError, match="provider_invalid_json_or_schema"):
        instance.complete("critic", "test", {}, Critique)


def test_connection_cut_during_http_read_becomes_abstention_error(monkeypatch):
    instance = client()

    def interrupted(*args, **kwargs):
        raise IncompleteRead(b"PRIVATE_PROVIDER_CONTENT")

    monkeypatch.setattr(instance.opener, "open", interrupted)
    with pytest.raises(ModelError, match="^provider_unreachable_or_timeout$"):
        instance.complete("critic", "test", {}, Critique)


def test_anthropic_messages_stream_for_qwen_uses_native_protocol(monkeypatch):
    instance = client()
    instance.settings = replace(
        instance.settings,
        critic=RoleConfig(
            "https://go.test/v1", "qwen", "test", api_format="anthropic", reasoning_effort="low"
        ),
    )
    events = [
        {"type": "message_start", "message": {"id": "msg_test"}},
        {"type": "content_block_delta", "delta": {"type": "thinking_delta", "thinking": "ignored"}},
        {
            "type": "content_block_delta",
            "delta": {
                "type": "text_delta",
                "text": '{"score":9,"approved":true,"complete":true,"issues":[]}',
            },
        },
        {"type": "message_delta", "delta": {"stop_reason": "end_turn"}},
        {"type": "message_stop"},
    ]
    raw = "".join("data: " + json.dumps(e) + "\n\n" for e in events).encode()

    def respond(request, **kwargs):
        body = json.loads(request.data)
        assert request.full_url == "https://go.test/v1/messages"
        assert request.get_header("X-api-key") == "test"
        assert request.get_header("Anthropic-version") == "2023-06-01"
        assert "system" in body and "response_format" not in body
        assert body["output_config"] == {"effort": "low"}
        return Stream(raw)

    monkeypatch.setattr(instance.opener, "open", respond)
    assert instance.complete("critic", "test", {}, Critique).approved


def test_large_reasoning_stream_does_not_consume_final_text_budget(monkeypatch):
    instance = client()
    reasoning = {"choices": [{"delta": {"reasoning_content": "r" * 10000}}]}
    final = {
        "choices": [
            {
                "delta": {"content": '{"score":9,"approved":true,"complete":true,"issues":[]}'},
                "finish_reason": "stop",
            }
        ]
    }
    raw = (
        ("data: " + json.dumps(reasoning) + "\n\n") * 220
        + "data: "
        + json.dumps(final)
        + "\n\ndata: [DONE]\n\n"
    )
    monkeypatch.setattr(instance.opener, "open", lambda *a, **k: Stream(raw.encode()))
    assert instance.complete("critic", "test", {}, Critique).score == 9


@pytest.mark.parametrize("thinking", [False, True, None])
def test_opt_in_schema_and_thinking_are_sent_only_to_configured_role(monkeypatch, thinking):
    instance = client()
    instance.settings = replace(
        instance.settings,
        critic=replace(instance.settings.critic, json_schema=True, enable_thinking=thinking),
    )
    requests = []
    raw = json.dumps(
        {
            "choices": [
                {
                    "finish_reason": "stop",
                    "message": {
                        "content": '{"score":9,"approved":true,"complete":true,"issues":[]}'
                    },
                }
            ]
        }
    ).encode()

    def respond(request, **kwargs):
        requests.append(json.loads(request.data))
        return io.BytesIO(raw)

    monkeypatch.setattr(instance.opener, "open", respond)
    assert instance.complete("critic", "test", {}, Critique).score == 9
    instance.complete("extractor", "test", {}, Critique)
    assert requests[0]["response_format"] == {
        "type": "json_schema",
        "json_schema": {"name": "Critique", "schema": Critique.model_json_schema(), "strict": True},
    }
    assert requests[0].get("enable_thinking") is thinking
    if thinking is None:
        assert "enable_thinking" not in requests[0]
    assert requests[1]["response_format"] == {"type": "json_object"}
    assert "enable_thinking" not in requests[1]


def test_structured_output_does_not_trust_invalid_provider_response(monkeypatch):
    instance = client()
    instance.settings = replace(
        instance.settings, critic=replace(instance.settings.critic, json_schema=True)
    )
    raw = b'{"choices":[{"finish_reason":"stop","message":{"content":"{\\"score\\":11}"}}]}'
    monkeypatch.setattr(instance.opener, "open", lambda *a, **k: io.BytesIO(raw))
    with pytest.raises(ModelError, match="provider_invalid_json_or_schema"):
        instance.complete("critic", "test", {}, Critique)
