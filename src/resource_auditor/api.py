import json
import threading
from pathlib import Path
from typing import Annotated

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import HTMLResponse
from starlette.concurrency import run_in_threadpool

from .config import Settings
from .engine import audit
from .evaluation import evaluate, load_truth
from .models import AuditResult, GroundTruth
from .pdf import read_pdf

app = FastAPI(title="NI 43-101 Resource Auditor", version="0.1.0")
# ponytail: two concurrent reports per process; use a job queue for multi-host service.
_slots = threading.BoundedSemaphore(2)


@app.get("/", response_class=HTMLResponse)
def index():
    return Path(__file__).with_name("index.html").read_text(encoding="utf-8")


@app.get("/health")
def health():
    try:
        settings = Settings.from_env()
        settings.validate()
        configured = True
    except ValueError:
        return {"status": "ok", "models_configured": False, "configuration_error": True}
    return {
        "status": "ok",
        "models_configured": configured,
        "extractor": settings.extractor.model,
        "critic": settings.critic.model,
        "pdf_parser": settings.pdf_parser,
        "mineru_configured": bool(settings.mineru_python),
    }


@app.get("/schema")
def schema():
    return {
        "result": AuditResult.model_json_schema(),
        "ground_truth": GroundTruth.model_json_schema(),
    }


async def bounded_read(file: UploadFile, limit: int) -> bytes:
    try:
        data = await file.read(limit + 1)
        if len(data) > limit:
            raise HTTPException(413, "Uploaded file exceeds configured size limit")
        return data
    finally:
        await file.close()


@app.post("/extract", response_model=AuditResult)
async def extract(
    file: Annotated[UploadFile, File()],
    pages: Annotated[str, Form()] = "",
    allow_jorc: Annotated[bool, Form()] = False,
    use_few_shots: Annotated[bool, Form()] = True,
):
    try:
        settings = Settings.from_env()
        settings.validate()
    except ValueError as exc:
        raise HTTPException(503, str(exc)) from None
    data = await bounded_read(file, settings.max_pdf_mb * 1024 * 1024)
    if not _slots.acquire(blocking=False):
        raise HTTPException(429, "Two reports are already running; retry later")

    def run():
        try:
            doc = read_pdf(data, Path(file.filename or "upload.pdf").name, settings, pages)
            return audit(doc, settings, allow_jorc=allow_jorc, use_few_shots=use_few_shots)
        finally:
            _slots.release()

    try:
        return await run_in_threadpool(run)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None
    except OSError:
        raise HTTPException(
            503, "Cannot read/write local evolution log; check data directory"
        ) from None


@app.post("/evaluate")
async def score(
    result: Annotated[UploadFile, File()],
    truth: Annotated[UploadFile, File()],
    mapping: Annotated[str, Form()] = "",
):
    # No Settings, provider, or audit initialization on this route.
    result_bytes = await bounded_read(result, 2 * 1024 * 1024)
    truth_bytes = await bounded_read(truth, 2 * 1024 * 1024)
    try:
        parsed_result = AuditResult.model_validate_json(result_bytes)
        parsed_mapping = json.loads(mapping) if mapping else None
        return evaluate(parsed_result, load_truth(truth_bytes, parsed_mapping))
    except (ValueError, KeyError, TypeError) as exc:
        raise HTTPException(
            422, "Invalid evaluation JSON, mapping, or document identity: " + type(exc).__name__
        ) from None
