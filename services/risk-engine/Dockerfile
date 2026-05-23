FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PORT=8080

WORKDIR /app

# Build context is the repo root so shared/ is available alongside the service.
COPY services/risk-engine/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY shared ./shared
# Flatten service source into /app so `main:app` resolves cleanly
# (the dashed dirname breaks Python's dotted-import path).
COPY services/risk-engine/ ./

ENV PYTHONPATH=/app

EXPOSE 8080
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8080"]
