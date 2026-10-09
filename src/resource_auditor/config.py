import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from dotenv import load_dotenv


@dataclass(frozen=True)
class RoleConfig:
    base_url: str
    model: str
    api_key: str
    api_format: str = "chat"
    reasoning_effort: str = ""

    def validate(self):
        parsed = urlsplit(self.base_url)
        if not self.model or not self.api_key:
            raise ValueError("Both roles require a model and an API key in local .env")
        local = parsed.hostname in {"localhost", "127.0.0.1", "::1"}
        if parsed.scheme != "https" and not (local and parsed.scheme == "http"):
            raise ValueError("Model base URL must use HTTPS (HTTP allowed only on loopback)")
        if (
            not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("Model base URL must not embed credentials, a query, or a fragment")
        if self.api_format not in {"chat", "anthropic"}:
            raise ValueError("API_FORMAT must be chat or anthropic")
        if self.reasoning_effort not in {"", "none", "low", "medium", "high", "xhigh", "max"}:
            raise ValueError("Invalid model reasoning effort")


@dataclass(frozen=True)
class Settings:
    extractor: RoleConfig
    critic: RoleConfig
    evolution_log: Path = Path("data/evolution.jsonl")
    json_mode: bool = True
    timeout: int = 120
    max_pdf_mb: int = 40
    max_context_chars: int = 100000
    max_selected_pages: int = 24
    pdf_parser: str = "auto"
    mineru_python: str = ""
    mineru_tier: str = "standard"
    mineru_timeout: int = 300
    stream: bool = True

    @classmethod
    def from_env(cls):
        load_dotenv(override=False)

        def role(prefix):
            return RoleConfig(
                *(
                    os.getenv(f"{prefix}_{key}", "").strip()
                    for key in ("BASE_URL", "MODEL", "API_KEY")
                ),
                api_format=os.getenv(f"{prefix}_API_FORMAT", "chat").strip().lower(),
                reasoning_effort=os.getenv(f"{prefix}_REASONING_EFFORT", "").strip().lower(),
            )

        return cls(
            extractor=role("EXTRACTOR"),
            critic=role("CRITIC"),
            evolution_log=Path(os.getenv("EVOLUTION_LOG", "data/evolution.jsonl")),
            json_mode=os.getenv("LLM_JSON_MODE", "true").lower() == "true",
            timeout=int(os.getenv("LLM_TIMEOUT_SECONDS", "120")),
            max_pdf_mb=int(os.getenv("MAX_PDF_MB", "40")),
            max_context_chars=int(os.getenv("MAX_CONTEXT_CHARS", "100000")),
            max_selected_pages=int(os.getenv("MAX_SELECTED_PAGES", "24")),
            pdf_parser=os.getenv("PDF_PARSER", "auto").strip().lower(),
            mineru_python=os.getenv("MINERU_PYTHON", "").strip(),
            mineru_tier=os.getenv("MINERU_TIER", "standard").strip().lower(),
            mineru_timeout=int(os.getenv("MINERU_TIMEOUT_SECONDS", "300")),
            stream=os.getenv("LLM_STREAM", "true").lower() == "true",
        )

    def validate(self):
        self.extractor.validate()
        self.critic.validate()
        if self.extractor.model.lower() == self.critic.model.lower():
            raise ValueError("Extractor and critic must use different model identifiers")
        if not 1 <= self.timeout <= 600 or not 1 <= self.max_pdf_mb <= 100:
            raise ValueError("Timeout must be 1..600 seconds and PDF limit 1..100 MB")
        if not 1000 <= self.max_context_chars <= 500000 or not 1 <= self.max_selected_pages <= 100:
            raise ValueError("Context/page limits are out of range")
        if self.pdf_parser not in {"auto", "pdfplumber", "mineru"}:
            raise ValueError("PDF_PARSER must be auto, pdfplumber, or mineru")
        if self.mineru_tier not in {"flash", "basic", "standard", "advanced"}:
            raise ValueError("Invalid MinerU tier")
        if not 1 <= self.mineru_timeout <= 1800:
            raise ValueError("MinerU timeout must be 1..1800 seconds")
