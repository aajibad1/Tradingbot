terraform {
  required_version = ">= 1.5.0"

  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 6.0"
    }
    google-beta = {
      source  = "hashicorp/google-beta"
      version = "~> 6.0"
    }
  }

  # Backend is configured per-environment (see infra/terraform/environments/*/main.tf).
  # The root module is consumed as a child by each environment.
}

provider "google" {
  project = var.project_id
  region  = var.region
  zone    = var.zone
}

provider "google-beta" {
  project = var.project_id
  region  = var.region
  zone    = var.zone
}

# ---------------------------------------------------------------------------
# Canonical lists (DRY): topics mirror shared/pubsub/publisher.py Topic enum,
# datasets mirror services/trade-ledger/schema/*.sql, services mirror services/.
# ---------------------------------------------------------------------------
locals {
  common_labels = merge(var.labels, {
    environment = var.environment
  })

  # Mirrors shared/pubsub/publisher.py:Topic — must stay 1:1 with the enum.
  topics = [
    "arb-market-data",
    "arb-funding-rates",
    "arb-opportunities",
    "arb-risk-alerts",
    "arb-trade-fills",
    "arb-ai-proposals",
    "arb-audit-log",
    # Provisioned ahead of its consumer so a publish to Topic.SENTIMENT_EVENTS
    # can never 404. The trade-ledger sentiment-history stream is still TODO,
    # hence no subscription below yet.
    "arb-sentiment-events",
  ]

  # Subscription convention: <topic>-<consumer-service>
  subscriptions = {
    "arb-market-data-opp-engine"     = "arb-market-data"
    "arb-funding-rates-opp-engine"   = "arb-funding-rates"
    "arb-opportunities-risk-engine"  = "arb-opportunities"
    "arb-opportunities-ledger"       = "arb-opportunities"
    "arb-opportunities-paper-trader" = "arb-opportunities"
    "arb-opportunities-orchestrator" = "arb-opportunities"
    "arb-risk-alerts-ledger"         = "arb-risk-alerts"
    "arb-risk-alerts-ai-ops"         = "arb-risk-alerts"
    "arb-market-data-ledger"         = "arb-market-data"
    "arb-trade-fills-ledger"         = "arb-trade-fills"
    "arb-trade-fills-risk-engine"    = "arb-trade-fills"
    "arb-ai-proposals-ledger"        = "arb-ai-proposals"
    "arb-audit-log-ledger"           = "arb-audit-log"
  }

  # BigQuery datasets — table-expiration rules:
  #   arb_market_data → 90d (high-volume ticks)
  #   arb_trading     → no expiration (7-year IRS / Form 8949 retention)
  #   arb_risk        → no expiration (forever; audit)
  #   arb_ai_ops      → 365d
  datasets = {
    arb_market_data = {
      description               = "High-volume tick data; 90-day table expiration."
      default_table_expiry_days = 90
    }
    arb_trading = {
      description               = "Trades + opportunities; retained 7 years for IRS Form 8949 compliance — DO NOT enable expiration."
      default_table_expiry_days = 0
    }
    arb_risk = {
      description               = "Risk events + kill-switch audit log; retained indefinitely."
      default_table_expiry_days = 0
    }
    arb_ai_ops = {
      description               = "AI proposals, evaluations, and feedback; 365-day expiration."
      default_table_expiry_days = 365
    }
  }

  # Services and the secrets each needs least-privilege access to.
  services = {
    "market-data" = {
      secrets        = ["COINBASE_API_KEY", "KRAKEN_API_KEY", "KRAKEN_SECRET", "CRYPTOCOM_API_KEY", "BINANCE_US_KEY", "HYPERLIQUID_KEY"]
      publish_topics = ["arb-market-data", "arb-audit-log"]
      subscribe_subs = []
      cpu_idle       = false # WebSocket connections require always-allocated CPU
    }
    "funding-rate-service" = {
      secrets        = ["COINGLASS_API_KEY", "ARBITRAGE_SCANNER_KEY"]
      publish_topics = ["arb-funding-rates", "arb-audit-log"]
      subscribe_subs = []
      cpu_idle       = true
    }
    "opportunity-engine" = {
      secrets        = []
      publish_topics = ["arb-opportunities", "arb-audit-log"]
      subscribe_subs = ["arb-market-data-opp-engine", "arb-funding-rates-opp-engine"]
      cpu_idle       = false
    }
    "risk-engine" = {
      secrets        = ["KILL_SWITCH_RESET_TOKEN"]
      publish_topics = ["arb-risk-alerts", "arb-audit-log"]
      # Subscribes to trade fills to maintain risk:* state (daily PnL, exposure,
      # concentration) so the drawdown/position-limit backstops have live data.
      subscribe_subs = ["arb-opportunities-risk-engine", "arb-trade-fills-risk-engine"]
      cpu_idle       = false
    }
    "paper-trader" = {
      secrets        = []
      publish_topics = ["arb-trade-fills", "arb-audit-log"]
      # Consumes APPROVED opportunities to simulate (was wired to its own output
      # topic arb-trade-fills — backwards; paper-trader's code subscribes here).
      subscribe_subs = ["arb-opportunities-paper-trader"]
      cpu_idle       = true
    }
    "trade-ledger" = {
      secrets        = []
      publish_topics = ["arb-audit-log"]
      subscribe_subs = [
        "arb-opportunities-ledger",
        "arb-trade-fills-ledger",
        "arb-risk-alerts-ledger",
        "arb-audit-log-ledger",
        "arb-ai-proposals-ledger",
        # Forward tick collection → arb_market_data.ticks (gated by env below).
        "arb-market-data-ledger",
      ]
      cpu_idle = true
      # Opt-in tick persistence for cross-exchange backtesting (downsampled + batched).
      env = { ENABLE_TICK_COLLECTION = "true" }
    }
    "ai-ops-agent" = {
      secrets        = ["ANTHROPIC_API_KEY", "GEMINI_API_KEY", "SLACK_WEBHOOK_URL"]
      publish_topics = ["arb-ai-proposals", "arb-audit-log"]
      subscribe_subs = ["arb-risk-alerts-ai-ops"]
      cpu_idle       = true
    }
    "execution-orchestrator" = {
      secrets        = ["COINBASE_API_KEY", "KRAKEN_API_KEY", "KRAKEN_SECRET", "CRYPTOCOM_API_KEY", "BINANCE_US_KEY", "HYPERLIQUID_KEY"]
      publish_topics = ["arb-trade-fills", "arb-audit-log"]
      # Consumes approved opportunities to route through human approval → Hummingbot
      # (its code subscribes to arb-opportunities-orchestrator; was missing here).
      subscribe_subs = ["arb-opportunities-orchestrator"]
      cpu_idle       = false
    }
    # Sentiment-service — Cloud Scheduler hits POST /sentiment/refresh every
    # 4h. The signal lives in Redis (sentiment:* namespace; sentiment-service
    # is the sole writer). No Pub/Sub topic yet — risk-engine reads Redis
    # synchronously in /evaluate.
    "sentiment-service" = {
      secrets        = ["PERPLEXITY_API_KEY", "CRYPTOPANIC_API_KEY"]
      publish_topics = ["arb-audit-log"]
      subscribe_subs = []
      cpu_idle       = true
    }
    # Notification dispatcher — country-routed WhatsApp/Telegram/SMS alerts with
    # channel fallback. Provider creds (Telegram/Twilio) added to `secrets` when
    # populated; degrades gracefully (no provider → reports undelivered).
    "notification-dispatcher" = {
      secrets        = []
      publish_topics = []
      subscribe_subs = []
      cpu_idle       = true
    }
    # Dashboard-api — the only browser-facing service. Aggregates Redis +
    # BigQuery into one /api/summary endpoint and serves the static dashboard.
    # Public (allUsers/run.invoker) so a browser can hit it without an ID
    # token. Read-only by design: no secrets, no Pub/Sub publish.
    "dashboard-api" = {
      secrets             = []
      publish_topics      = []
      subscribe_subs      = []
      cpu_idle            = true
      allow_public_invoke = true
      bigquery_reader     = true
    }
  }

  # Flat list of every distinct secret ID used anywhere in the system.
  all_secret_ids = distinct(flatten([for s in local.services : s.secrets]))

  artifact_registry_prefix = "${var.region}-docker.pkg.dev/${var.project_id}/${var.artifact_registry_repo}"
}

# ---------------------------------------------------------------------------
# Required APIs
# ---------------------------------------------------------------------------
resource "google_project_service" "required" {
  for_each = toset([
    "run.googleapis.com",
    "pubsub.googleapis.com",
    "bigquery.googleapis.com",
    "secretmanager.googleapis.com",
    "redis.googleapis.com",
    "artifactregistry.googleapis.com",
    "vpcaccess.googleapis.com",
    "monitoring.googleapis.com",
    "logging.googleapis.com",
    "cloudresourcemanager.googleapis.com",
    "iam.googleapis.com",
    "cloudscheduler.googleapis.com",
  ])

  project            = var.project_id
  service            = each.value
  disable_on_destroy = false
}

# ---------------------------------------------------------------------------
# Artifact Registry — single repo for all service images
# ---------------------------------------------------------------------------
resource "google_artifact_registry_repository" "crypto_arb" {
  location      = var.region
  repository_id = var.artifact_registry_repo
  description   = "Container images for the crypto arbitrage microservices."
  format        = "DOCKER"
  labels        = local.common_labels

  depends_on = [google_project_service.required]
}

# ---------------------------------------------------------------------------
# VPC + Serverless connector — private path from Cloud Run to Memorystore
# ---------------------------------------------------------------------------
resource "google_compute_network" "vpc" {
  name                    = "arb-vpc-${var.environment}"
  auto_create_subnetworks = false
  depends_on              = [google_project_service.required]
}

resource "google_compute_subnetwork" "vpc_subnet" {
  name          = "arb-subnet-${var.environment}"
  ip_cidr_range = "10.10.0.0/24"
  region        = var.region
  network       = google_compute_network.vpc.id
}

resource "google_vpc_access_connector" "connector" {
  name           = "arb-conn-${var.environment}"
  region         = var.region
  ip_cidr_range  = "10.8.0.0/28"
  network        = google_compute_network.vpc.name
  min_throughput = 200
  max_throughput = 300

  depends_on = [google_project_service.required]
}

# ---------------------------------------------------------------------------
# Pub/Sub
# ---------------------------------------------------------------------------
module "pubsub" {
  source = "./modules/pubsub"

  project_id    = var.project_id
  topics        = local.topics
  subscriptions = local.subscriptions
  labels        = local.common_labels

  depends_on = [google_project_service.required]
}

# ---------------------------------------------------------------------------
# BigQuery
# ---------------------------------------------------------------------------
module "bigquery" {
  source = "./modules/bigquery"

  project_id = var.project_id
  region     = var.region
  datasets   = local.datasets
  labels     = local.common_labels

  depends_on = [google_project_service.required]
}

# ---------------------------------------------------------------------------
# Memorystore Redis (risk-engine state)
# ---------------------------------------------------------------------------
module "memorystore" {
  source = "./modules/memorystore"

  project_id     = var.project_id
  region         = var.region
  name           = "arb-risk-redis-${var.environment}"
  tier           = var.redis_tier
  memory_size_gb = var.redis_memory_size_gb
  network_id     = google_compute_network.vpc.id
  labels         = local.common_labels

  depends_on = [google_project_service.required]
}

# ---------------------------------------------------------------------------
# Secret Manager — create every distinct secret, no values written here
# ---------------------------------------------------------------------------
module "secrets" {
  source = "./modules/secret-manager"

  project_id = var.project_id
  secret_ids = local.all_secret_ids
  labels     = local.common_labels

  depends_on = [google_project_service.required]
}

# ---------------------------------------------------------------------------
# Cloud Run services — one per microservice, least-privilege IAM per service
# ---------------------------------------------------------------------------
module "cloud_run" {
  source = "./modules/cloud-run"

  for_each = local.services

  project_id          = var.project_id
  region              = var.region
  service_name        = each.key
  image               = "${local.artifact_registry_prefix}/${each.key}:${var.image_tag}"
  min_instances       = var.cloud_run_min_instances
  max_instances       = var.cloud_run_max_instances
  cpu_idle            = each.value.cpu_idle
  vpc_connector       = google_vpc_access_connector.connector.id
  publish_topics      = each.value.publish_topics
  subscribe_subs      = each.value.subscribe_subs
  secrets             = each.value.secrets
  allow_public_invoke = lookup(each.value, "allow_public_invoke", false)
  bigquery_reader     = lookup(each.value, "bigquery_reader", false)
  # Public services accept internet ingress; everything else is internal-only
  # (Cloud Scheduler / Pub/Sub / VPC still reach it). Defence-in-depth beyond IAM.
  ingress = lookup(each.value, "allow_public_invoke", false) ? "INGRESS_TRAFFIC_ALL" : "INGRESS_TRAFFIC_INTERNAL_ONLY"
  env_vars = merge({
    GCP_PROJECT_ID = var.project_id
    ENVIRONMENT    = var.environment
    # rediss:// (TLS) + AUTH string — redis-py's from_url handles both natively.
    # ssl_cert_reqs=none encrypts in transit without shipping Memorystore's
    # self-signed CA into every image; pin the CA (module.memorystore.server_ca_certs)
    # for full verification as a follow-up.
    REDIS_URL = "rediss://:${module.memorystore.auth_string}@${module.memorystore.host}:${module.memorystore.port}/0?ssl_cert_reqs=none"
  }, lookup(each.value, "env", {}))
  labels = local.common_labels

  depends_on = [
    module.pubsub,
    module.secrets,
    google_artifact_registry_repository.crypto_arb,
  ]
}

# ---------------------------------------------------------------------------
# Cloud Scheduler — hits sentiment-service every 4 hours.
# Uses an OIDC-authenticated invocation so the sentiment-service can stay
# --no-allow-unauthenticated.
# ---------------------------------------------------------------------------
resource "google_service_account" "sentiment_scheduler" {
  account_id   = "sentiment-scheduler-${var.environment}"
  display_name = "Cloud Scheduler invoker for sentiment-service"
  project      = var.project_id

  depends_on = [google_project_service.required]
}

resource "google_cloud_run_service_iam_member" "sentiment_invoker" {
  project  = var.project_id
  location = var.region
  service  = "sentiment-service"
  role     = "roles/run.invoker"
  member   = "serviceAccount:${google_service_account.sentiment_scheduler.email}"

  depends_on = [module.cloud_run]
}

resource "google_cloud_scheduler_job" "sentiment_refresh" {
  name        = "sentiment-refresh-${var.environment}"
  description = "Refresh market-sentiment signal in Redis every 4 hours."
  schedule    = "0 */4 * * *"
  time_zone   = "Etc/UTC"
  project     = var.project_id
  region      = var.region

  http_target {
    http_method = "POST"
    uri         = "${module.cloud_run["sentiment-service"].service_url}/sentiment/refresh"

    oidc_token {
      service_account_email = google_service_account.sentiment_scheduler.email
      audience              = module.cloud_run["sentiment-service"].service_url
    }
  }

  depends_on = [google_cloud_run_service_iam_member.sentiment_invoker]
}

# ---------------------------------------------------------------------------
# Cloud Scheduler — resets risk:daily_pnl_usd at UTC midnight so the drawdown
# guard's daily-loss window rolls over. Without this the daily PnL accumulates
# forever and the daily-loss limit would eventually trip on lifetime losses.
# OIDC-authenticated so risk-engine stays --no-allow-unauthenticated.
# ---------------------------------------------------------------------------
resource "google_service_account" "risk_reset_scheduler" {
  account_id   = "risk-reset-scheduler-${var.environment}"
  display_name = "Cloud Scheduler invoker for risk-engine daily reset"
  project      = var.project_id

  depends_on = [google_project_service.required]
}

resource "google_cloud_run_service_iam_member" "risk_reset_invoker" {
  project  = var.project_id
  location = var.region
  service  = "risk-engine"
  role     = "roles/run.invoker"
  member   = "serviceAccount:${google_service_account.risk_reset_scheduler.email}"

  depends_on = [module.cloud_run]
}

resource "google_cloud_scheduler_job" "risk_daily_reset" {
  name        = "risk-daily-pnl-reset-${var.environment}"
  description = "Reset risk:daily_pnl_usd at UTC midnight (drawdown-window rollover)."
  schedule    = "0 0 * * *"
  time_zone   = "Etc/UTC"
  project     = var.project_id
  region      = var.region

  http_target {
    http_method = "POST"
    uri         = "${module.cloud_run["risk-engine"].service_url}/positions/reset-daily"

    oidc_token {
      service_account_email = google_service_account.risk_reset_scheduler.email
      audience              = module.cloud_run["risk-engine"].service_url
    }
  }

  depends_on = [google_cloud_run_service_iam_member.risk_reset_invoker]
}

# ---------------------------------------------------------------------------
# Monitoring + alerting
# ---------------------------------------------------------------------------
module "monitoring" {
  source = "./modules/monitoring"
  count  = var.enable_monitoring_alerts ? 1 : 0

  project_id              = var.project_id
  environment             = var.environment
  slack_webhook_secret_id = var.slack_webhook_secret_id
  service_names           = keys(local.services)
  subscription_names      = keys(local.subscriptions)
  labels                  = local.common_labels

  depends_on = [
    module.cloud_run,
    module.pubsub,
    google_project_service.required,
  ]
}
