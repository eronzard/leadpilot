FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    PORT=7860 \
    LEADPILOT_DB_PATH=/data/leadpilot.db

# Non-root user (also what Hugging Face Spaces expects: uid 1000).
RUN useradd -m -u 1000 app && mkdir -p /data && chown app /data
WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY --chown=app . .
USER app

EXPOSE 7860
HEALTHCHECK --interval=30s --timeout=5s CMD python -c "import urllib.request,os;urllib.request.urlopen(f'http://127.0.0.1:{os.environ[\"PORT\"]}/healthz')"
CMD ["python", "-m", "leadpilot"]
