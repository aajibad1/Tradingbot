---
name: sentiment-service-agent
description: Builds the sentiment-service — a lightweight microservice that runs every 4 hours, queries Perplexity Sonar API + Alternative.me Fear & Greed + CryptoPanic for real-time market sentiment, stores scores in Redis, and feeds a sentiment gate into the opportunity engine to block trades during bearish conditions.
model: claude-sonnet-4-5
tools:
  - Read
  - Write
  - Bash
---

You are building the sentiment-service for the crypto arbitrage system. GCP project: "agenuit".

## Context
Market sentiment directly controls funding rates. When sentiment is bullish, funding rates are elevated and arbitrage is profitable. When sentiment turns bearish, funding collapses and trading costs exceed gains. This service detects sentiment shifts 2–6 hours early and gates the opportunity engine accordingly.

## Architecture
```
Perplexity Sonar API + Alternative.me + CryptoPanic
              ↓ (every 4 hours)
    sentiment-service (Cloud Run scheduled job)
              ↓
    Redis keys: sentiment:score:{symbol}, sentiment:macro:fear_greed
              ↓
    opportunity-engine reads before scoring any trade
              ↓
    Trades blocked when sentiment_score < -0.1 (bearish)
    Trades allowed when sentiment_score >= -0.1
```

## Files to Create

### services/sentiment-service/main.py
```python
import asyncio
import json
import logging
import os
from datetime import datetime

import httpx
from redis.asyncio import Redis
from google.cloud import secretmanager

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

SYMBOLS = ["BTC", "ETH", "SOL", "BNB", "XRP"]
REFRESH_INTERVAL_SECONDS = 4 * 3600  # 4 hours


class SentimentService:
    def __init__(self, redis: Redis, perplexity_key: str, cryptopanic_key: str):
        self.redis = redis
        self.perplexity_key = perplexity_key
        self.cryptopanic_key = cryptopanic_key

    async def run(self):
        logger.info("Sentiment service started")
        while True:
            try:
                await self.refresh_all()
            except Exception as e:
                logger.error(f"Sentiment refresh failed: {e}")
            await asyncio.sleep(REFRESH_INTERVAL_SECONDS)

    async def refresh_all(self):
        # Macro signal first (free, fast)
        await self.update_fear_greed()
        # Per-symbol signals
        for symbol in SYMBOLS:
            await self.update_symbol_sentiment(symbol)
        logger.info(f"Sentiment refresh complete at {datetime.utcnow().isoformat()}")

    async def update_fear_greed(self):
        """Alternative.me Fear & Greed Index — free, no key required."""
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(
                    "https://api.alternative.me/fng/?limit=1&format=json"
                )
                data = resp.json()
                value = int(data["data"][0]["value"])
                classification = data["data"][0]["value_classification"]
                await self.redis.set("sentiment:macro:fear_greed_index", value)
                await self.redis.set("sentiment:macro:fear_greed_label", classification)
                await self.redis.set(
                    "sentiment:macro:updated_at", datetime.utcnow().isoformat()
                )
                logger.info(f"Fear & Greed: {value} ({classification})")
        except Exception as e:
            logger.warning(f"Fear & Greed fetch failed: {e}")

    async def update_symbol_sentiment(self, symbol: str):
        """Perplexity Sonar — real-time news + social synthesis."""
        try:
            score, direction, drivers, confidence = await self.query_perplexity(symbol)
            await self.redis.hset(
                f"sentiment:symbol:{symbol}",
                mapping={
                    "score": score,
                    "direction": direction,
                    "confidence": confidence,
                    "drivers": json.dumps(drivers),
                    "updated_at": datetime.utcnow().isoformat(),
                },
            )
            # Simple float key for fast reads in opportunity engine
            await self.redis.set(f"sentiment:score:{symbol}", score)
            logger.info(f"Sentiment {symbol}: score={score:.2f} dir={direction}")
        except Exception as e:
            logger.warning(f"Perplexity sentiment failed for {symbol}: {e}")
            # On failure — do NOT block trades, just log
            await self.redis.set(f"sentiment:score:{symbol}", "0.0")

    async def query_perplexity(self, symbol: str) -> tuple:
        prompt = f"""Analyze current market sentiment for {symbol}/USDT crypto asset right now.

Return ONLY valid JSON with these exact fields:
{{
  "sentiment_score": <float from -1.0 (extreme fear) to 1.0 (extreme greed)>,
  "direction": "<bullish|bearish|neutral>",
  "funding_outlook": "<rising|falling|stable>",
  "key_drivers": ["<driver 1>", "<driver 2>", "<driver 3>"],
  "confidence": <float 0.0 to 1.0>
}}

Base this on: latest news from the past 4 hours, social media sentiment,
derivatives funding rates, spot price momentum, and any macro events.
Be precise and data-driven. Do not add any text outside the JSON."""

        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                "https://api.perplexity.ai/chat/completions",
                headers={
                    "Authorization": f"Bearer {self.perplexity_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": "sonar",
                    "messages": [{"role": "user", "content": prompt}],
                    "search_recency_filter": "hour",
                    "temperature": 0.1,
                    "max_tokens": 300,
                },
            )
            resp.raise_for_status()
            content = resp.json()["choices"][0]["message"]["content"]
            # Parse JSON from response
            start = content.find("{")
            end = content.rfind("}") + 1
            parsed = json.loads(content[start:end])
            return (
                float(parsed["sentiment_score"]),
                parsed["direction"],
                parsed.get("key_drivers", []),
                float(parsed.get("confidence", 0.5)),
            )


def load_secret(name: str) -> str:
    client = secretmanager.SecretManagerServiceClient()
    project = os.getenv("GCP_PROJECT_ID", "agenuit")
    resource = f"projects/{project}/secrets/{name}/versions/latest"
    return client.access_secret_version(name=resource).payload.data.decode("utf-8")


async def main():
    redis = Redis.from_url(os.getenv("REDIS_URL", "redis://localhost:6379"))
    perplexity_key = load_secret("PERPLEXITY_API_KEY")
    cryptopanic_key = load_secret("CRYPTOPANIC_API_KEY")
    service = SentimentService(redis, perplexity_key, cryptopanic_key)
    await service.run()


if __name__ == "__main__":
    asyncio.run(main())
```

### services/sentiment-service/sentiment_gate.py
```python
"""
SentimentGate — imported by opportunity-engine to check sentiment before scoring.
Reads from Redis. Never makes external API calls (hot path safe).
"""
import logging
from redis.asyncio import Redis

logger = logging.getLogger(__name__)

SENTIMENT_BLOCK_THRESHOLD = -0.1    # block trades below this score
FEAR_GREED_BLOCK_THRESHOLD = 20     # block trades when extreme fear (0-100 scale)
SENTIMENT_STALENESS_HOURS = 6       # ignore stale data older than 6 hours


class SentimentGate:
    def __init__(self, redis: Redis):
        self.redis = redis

    async def is_allowed(self, symbol: str) -> tuple[bool, str]:
        """
        Returns (allowed: bool, reason: str).
        On any Redis error or missing data — allow the trade (fail open).
        """
        try:
            # Check macro fear/greed first (fastest check)
            fear_greed = await self.redis.get("sentiment:macro:fear_greed_index")
            if fear_greed and int(fear_greed) < FEAR_GREED_BLOCK_THRESHOLD:
                return False, f"Macro fear/greed={fear_greed} below threshold {FEAR_GREED_BLOCK_THRESHOLD}"

            # Check symbol-specific score
            score = await self.redis.get(f"sentiment:score:{symbol}")
            if score is None:
                logger.debug(f"No sentiment data for {symbol} — allowing trade")
                return True, "No sentiment data — fail open"

            score_float = float(score)
            if score_float < SENTIMENT_BLOCK_THRESHOLD:
                return False, f"{symbol} sentiment={score_float:.2f} below threshold {SENTIMENT_BLOCK_THRESHOLD}"

            return True, f"{symbol} sentiment={score_float:.2f} OK"

        except Exception as e:
            logger.warning(f"SentimentGate Redis error: {e} — failing open")
            return True, f"Sentiment gate error (fail open): {e}"
```

### services/sentiment-service/requirements.txt
```
httpx==0.27.0
redis[hiredis]==5.0.8
google-cloud-secret-manager==2.20.0
```

### services/sentiment-service/Dockerfile
```dockerfile
FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
ENV PYTHONUNBUFFERED=1
CMD ["python", "main.py"]
```

### services/sentiment-service/health.py
```python
"""Minimal /healthz endpoint for Cloud Run health checks."""
from http.server import HTTPServer, BaseHTTPRequestHandler
import threading

class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/healthz":
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"OK")
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, *args):
        pass  # suppress access logs

def start_health_server(port: int = 8080):
    server = HTTPServer(("0.0.0.0", port), HealthHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
```

## Integration: Wire SentimentGate Into Opportunity Engine

Find the existing opportunity scorer (likely `services/opportunity-engine/scorer.py` or similar).
Add the sentiment gate check. Do NOT restructure the existing scorer — just add the gate as an early return:

```python
# Add at the top of the evaluate/score method, AFTER kill switch check, BEFORE fee math
from sentiment_gate import SentimentGate  # adjust import path as needed

# In the scorer class __init__:
self.sentiment_gate = SentimentGate(self.redis)

# In evaluate():
allowed, reason = await self.sentiment_gate.is_allowed(opportunity.base_asset)
if not allowed:
    logger.info(f"SENTIMENT GATE BLOCKED: {reason}")
    return None
```

## GCP Secret to Add
```bash
# Add to GCP Secret Manager
echo -n "pplx-YOUR_PERPLEXITY_KEY" | gcloud secrets versions add PERPLEXITY_API_KEY --data-file=-
echo -n "YOUR_CRYPTOPANIC_KEY" | gcloud secrets versions add CRYPTOPANIC_API_KEY --data-file=-
```

## Get API Keys
- Perplexity: https://www.perplexity.ai/settings/api (generate key, ~$5/mo at 6 assets × 6 calls/day)
- CryptoPanic: https://cryptopanic.com/developers/api/ (free tier, 50 req/hr)
- Alternative.me Fear & Greed: FREE, no key needed

## Completion Report
```
SENTIMENT SERVICE COMPLETE
Files created:
  services/sentiment-service/main.py
  services/sentiment-service/sentiment_gate.py
  services/sentiment-service/requirements.txt
  services/sentiment-service/Dockerfile
  services/sentiment-service/health.py
Integrated: SentimentGate wired into opportunity-engine scorer
Signals:
  - Alternative.me Fear & Greed (free, macro filter)
  - Perplexity Sonar (real-time, per-symbol, ~$5/mo)
  - CryptoPanic (free tier, news filter)
Behavior: Fails OPEN on missing/stale data (never blocks on errors)
Cost: ~$5.40/mo at 6 assets × 6 calls/day on sonar model
```
