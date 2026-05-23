terraform {
  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 6.0"
    }
  }
}

# ---------------------------------------------------------------------------
# Datasets
# ---------------------------------------------------------------------------
resource "google_bigquery_dataset" "dataset" {
  for_each = var.datasets

  project       = var.project_id
  dataset_id    = each.key
  friendly_name = each.key
  description   = each.value.description
  location      = var.region
  labels        = var.labels

  # 0 -> never expire (7-year tax retention for arb_trading, indefinite for arb_risk)
  default_table_expiration_ms = each.value.default_table_expiry_days > 0 ? each.value.default_table_expiry_days * 86400000 : null
}

# ---------------------------------------------------------------------------
# Tables — schemas live in schemas/*.json (must match services/trade-ledger/schema/*.sql)
# ---------------------------------------------------------------------------
locals {
  tables = {
    trades = {
      dataset           = "arb_trading"
      schema_file       = "${path.module}/schemas/trades.json"
      time_partitioning = "opened_at"
      clustering        = ["trade_type", "asset"]
    }
    opportunities = {
      dataset           = "arb_trading"
      schema_file       = "${path.module}/schemas/opportunities.json"
      time_partitioning = "detected_at"
      clustering        = ["strategy", "asset"]
    }
    funding_events = {
      dataset           = "arb_trading"
      schema_file       = "${path.module}/schemas/funding_events.json"
      time_partitioning = "observed_at"
      clustering        = ["exchange", "asset"]
    }
    risk_events = {
      dataset           = "arb_risk"
      schema_file       = "${path.module}/schemas/risk_events.json"
      time_partitioning = "emitted_at"
      clustering        = ["alert_type", "severity"]
    }
    audit_log = {
      dataset           = "arb_risk"
      schema_file       = "${path.module}/schemas/audit_log.json"
      time_partitioning = "emitted_at"
      clustering        = ["source", "event"]
    }
  }
}

resource "google_bigquery_table" "table" {
  for_each = local.tables

  project             = var.project_id
  dataset_id          = google_bigquery_dataset.dataset[each.value.dataset].dataset_id
  table_id            = each.key
  schema              = file(each.value.schema_file)
  labels              = var.labels
  deletion_protection = true # IRS retention; do not allow accidental destroy

  time_partitioning {
    type  = "DAY"
    field = each.value.time_partitioning
  }

  clustering = each.value.clustering
}
