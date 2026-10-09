import json
import time
import urllib.error
import urllib.request
import uuid
from http.client import HTTPException

from pydantic import ValidationError

from .config import Settings


class ModelError(RuntimeError):
    """Sanitized provider failure; never include headers, keys or response bodies."""


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class ModelClient:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.opener = urllib.request.build_opener(NoRedirect())
        self.session_id = uuid.uuid4().hex

    def read_stream(self, response, api_format="chat") -> str:
        size, text_size, finish, content, stopped = 0, 0, None, [], False
        deadline = time.monotonic() + self.settings.timeout
        while line := response.readline(262145):
            size += len(line)
            if size > 16 * 1024 * 1024 or len(line) > 262144:
                raise ModelError("provider_response_too_large")
            if time.monotonic() > deadline:
                raise ModelError("provider_unreachable_or_timeout")
            if not line.startswith(b"data:"):
                continue
            data = line[5:].strip()
            if data == b"[DONE]":
                break
            event = json.loads(data)
            if api_format == "anthropic":
                if event.get("type") == "error":
                    raise ModelError("provider_stream_error")
                delta = event.get("delta", {})
                if delta.get("type") == "text_delta":
                    text = delta["text"]
                    if not isinstance(text, str):
                        raise ModelError("provider_response_not_text")
                    content.append(text)
                    text_size += len(text)
                if event.get("type") == "message_delta":
                    finish = delta.get("stop_reason", finish)
                if event.get("type") == "message_stop":
                    stopped = True
                    break
                if text_size > 2 * 1024 * 1024:
                    raise ModelError("provider_response_too_large")
                continue
            for choice in event.get("choices", []):
                if choice.get("index", 0) != 0:
                    continue
                delta = choice.get("delta", {}).get("content")
                if delta is not None:
                    if not isinstance(delta, str):
                        raise ModelError("provider_response_not_text")
                    content.append(delta)
                    text_size += len(delta)
                    if text_size > 2 * 1024 * 1024:
                        raise ModelError("provider_response_too_large")
                if choice.get("finish_reason"):
                    finish = choice["finish_reason"]
        if (api_format == "chat" and finish != "stop") or (
            api_format == "anthropic" and (finish != "end_turn" or not stopped)
        ):
            raise ModelError("provider_response_incomplete")
        return "".join(content)

    def complete(self, role: str, system: str, payload: dict, schema):
        config = getattr(self.settings, role)
        system = (
            system
            + "\nReturn only JSON matching this schema:\n"
            + json.dumps(schema.model_json_schema())
        )
        body = {
            "model": config.model,
            "messages": [
                {
                    "role": "system",
                    "content": system,
                },
                {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
            ],
            "max_tokens": 12000,
            "stream": self.settings.stream,
        }
        headers = {
            "Authorization": "Bearer " + config.api_key,
            "Content-Type": "application/json",
            "User-Agent": "ni43-resource-auditor/0.1",
            "x-opencode-session": self.session_id + ":" + role,
        }
        endpoint = "/chat/completions"
        if config.api_format == "anthropic":
            endpoint = "/messages"
            body["system"] = system
            body["messages"] = body["messages"][1:]
            headers.update({"x-api-key": config.api_key, "anthropic-version": "2023-06-01"})
        if self.settings.json_mode and config.api_format == "chat":
            body["response_format"] = {"type": "json_object"}
        if config.reasoning_effort:
            if config.api_format == "anthropic":
                body["output_config"] = {"effort": config.reasoning_effort}
            else:
                body["reasoning_effort"] = config.reasoning_effort
        request = urllib.request.Request(
            config.base_url.rstrip("/") + endpoint,
            json.dumps(body).encode("utf-8"),
            headers,
            method="POST",
        )
        for retry in range(2):
            try:
                with self.opener.open(request, timeout=self.settings.timeout) as response:
                    headers = getattr(response, "headers", {})
                    if "text/event-stream" in headers.get("Content-Type", ""):
                        content = self.read_stream(response, config.api_format)
                    else:
                        raw = response.read(2 * 1024 * 1024 + 1)
                        if len(raw) > 2 * 1024 * 1024:
                            raise ModelError("provider_response_too_large")
                        envelope = json.loads(raw)
                        if config.api_format == "anthropic":
                            if envelope.get("stop_reason") != "end_turn":
                                raise ModelError("provider_response_incomplete")
                            content = "".join(
                                block["text"]
                                for block in envelope["content"]
                                if block["type"] == "text"
                            )
                        else:
                            choice = envelope["choices"][0]
                            if choice.get("finish_reason") not in (None, "stop"):
                                raise ModelError("provider_response_incomplete")
                            content = choice["message"]["content"]
                if not isinstance(content, str):
                    raise ModelError("provider_response_not_text")
                content = content.strip()
                if content.startswith("```json\n") and content.endswith("```"):
                    content = content[8:-3].strip()
                return schema.model_validate_json(content)
            except urllib.error.HTTPError as exc:
                if exc.code in (429, 502, 503, 504) and retry == 0:
                    time.sleep(1)
                    continue
                raise ModelError(f"provider_http_{exc.code}") from None
            except (urllib.error.URLError, TimeoutError, OSError, HTTPException):
                raise ModelError("provider_unreachable_or_timeout") from None
            except (ValueError, KeyError, IndexError, TypeError, AttributeError, ValidationError):
                raise ModelError("provider_invalid_json_or_schema") from None
        raise ModelError("provider_retry_exhausted")
