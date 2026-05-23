FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PORT=8080

WORKDIR /app

COPY services/dashboard-api/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY shared ./shared
COPY services/dashboard-api/ ./
# Bundle the static dashboard HTML alongside the API so a single Cloud Run
# service serves both the page and the data it fetches.
COPY dashboard ./static

ENV PYTHONPATH=/app

EXPOSE 8080
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8080"]
