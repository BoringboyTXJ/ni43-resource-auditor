FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=/app/src
WORKDIR /app
COPY requirements.txt ./
RUN python -m pip install --no-cache-dir --require-hashes -r requirements.txt \
    && useradd --create-home --uid 10001 auditor \
    && mkdir -p /app/data && chown auditor:auditor /app/data
COPY src/ ./src/
USER auditor
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3)"
CMD ["python", "-m", "uvicorn", "resource_auditor.api:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
