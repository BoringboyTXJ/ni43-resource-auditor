FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=/app/src \
    MINERU_PYTHON=/opt/mineru/bin/python MINERU_TIER=basic \
    MINERU_HOME=/opt/mineru-home MINERU_MODEL_SMALL_BACKEND=onnx \
    MINERU_MODEL_SOURCE=local MINERU_MODEL_VLM_ENGINE=llama-cpp HF_HUB_DISABLE_XET=1
WORKDIR /app
RUN apt-get update \
    && apt-get install -y --no-install-recommends libglib2.0-0 libgomp1 libstdc++6 fonts-noto-cjk \
    && rm -rf /var/lib/apt/lists/*
COPY requirements.txt requirements-mineru.txt ./
RUN python -m pip install --no-cache-dir --require-hashes -r requirements.txt \
    && python -m venv /opt/mineru \
    && /opt/mineru/bin/pip install --no-cache-dir --require-hashes -r requirements-mineru.txt \
    && useradd --create-home --uid 10001 auditor \
    && mkdir -p /app/data /opt/mineru-home \
    && chown auditor:auditor /app/data /opt/mineru-home
COPY src/ ./src/
USER auditor
# Download as the runtime user: model markers/locks and cache permissions match.
# Missing models fail the build; running containers never fetch OCR weights.
RUN /opt/mineru/bin/mineru-kit models download --tier standard --small-backend onnx --vlm-engine llama-cpp --source auto \
    && /opt/mineru/bin/mineru-kit models verify --tier standard --small-backend onnx --vlm-engine llama-cpp
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3)"
CMD ["python", "-m", "uvicorn", "resource_auditor.api:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
