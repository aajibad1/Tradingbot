terraform {
  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 6.0"
    }
  }
}

# ---------------------------------------------------------------------------
# Notification channel — Slack OAuth token fetched from Secret Manager
#
# Gated by ``enable_slack_alert_channel`` because GCP's native Slack channel
# requires a Slack App OAuth bot token (``xoxb-...``), not a webhook URL.
# When disabled, alert policies still fire — they just don't push to Slack;
# you'll see them in the GCP Monitoring UI and (separately) the runtime
# services still post to the SLACK_WEBHOOK_URL secret directly.
# ---------------------------------------------------------------------------
data "google_secret_manager_secret_version" "slack" {
  count   = var.enable_slack_alert_channel ? 1 : 0
  project = var.project_id
  secret  = var.slack_webhook_secret_id
}

resource "google_monitoring_notification_channel" "slack" {
  count        = var.enable_slack_alert_channel ? 1 : 0
  project      = var.project_id
  display_name = "Slack alerts (${var.environment})"
  type         = "slack"
  labels = {
    channel_name = "#arb-alerts"
  }
  sensitive_labels {
    auth_token = data.google_secret_manager_secret_version.slack[0].secret_data
  }
}

locals {
  notification_channels = var.enable_slack_alert_channel ? [
    google_monitoring_notification_channel.slack[0].id
  ] : []
}

# ---------------------------------------------------------------------------
# Log-based metric: kill switch activations
# ---------------------------------------------------------------------------
resource "google_logging_metric" "kill_switch" {
  project = var.project_id
  name    = "arb/kill_switch_activated"
  filter  = "resource.type=\"cloud_run_revision\" AND textPayload:\"KILL_SWITCH_ACTIVATED\""

  metric_descriptor {
    metric_kind  = "DELTA"
    value_type   = "INT64"
    unit         = "1"
    display_name = "Kill switch activation events"
  }
}

# ---------------------------------------------------------------------------
# Alert: Cloud Run 5xx error rate > threshold
# ---------------------------------------------------------------------------
resource "google_monitoring_alert_policy" "cloud_run_error_rate" {
  project      = var.project_id
  display_name = "Cloud Run 5xx > ${var.cloud_run_error_rate_threshold * 100}% (${var.environment})"
  combiner     = "OR"
  user_labels  = var.labels

  conditions {
    display_name = "5xx fraction high"
    condition_threshold {
      filter          = "metric.type=\"run.googleapis.com/request_count\" resource.type=\"cloud_run_revision\" metric.label.\"response_code_class\"=\"5xx\""
      comparison      = "COMPARISON_GT"
      threshold_value = var.cloud_run_error_rate_threshold
      duration        = "300s"
      aggregations {
        alignment_period   = "60s"
        per_series_aligner = "ALIGN_RATE"
      }
    }
  }

  notification_channels = local.notification_channels
  alert_strategy {
    auto_close = "1800s"
  }
}

# ---------------------------------------------------------------------------
# Alert: Pub/Sub subscription oldest_unacked_message_age > threshold
# ---------------------------------------------------------------------------
resource "google_monitoring_alert_policy" "pubsub_backlog" {
  project      = var.project_id
  display_name = "Pub/Sub backlog > ${var.subscription_age_threshold_seconds}s (${var.environment})"
  combiner     = "OR"
  user_labels  = var.labels

  conditions {
    display_name = "Unacked message age too high"
    condition_threshold {
      filter          = "metric.type=\"pubsub.googleapis.com/subscription/oldest_unacked_message_age\" resource.type=\"pubsub_subscription\""
      comparison      = "COMPARISON_GT"
      threshold_value = var.subscription_age_threshold_seconds
      duration        = "120s"
      aggregations {
        alignment_period   = "60s"
        per_series_aligner = "ALIGN_MAX"
      }
    }
  }

  notification_channels = local.notification_channels
  alert_strategy {
    auto_close = "1800s"
  }
}

# ---------------------------------------------------------------------------
# Alert: Kill switch activation
# ---------------------------------------------------------------------------
resource "google_monitoring_alert_policy" "kill_switch" {
  project      = var.project_id
  display_name = "KILL SWITCH ACTIVATED (${var.environment})"
  combiner     = "OR"
  user_labels  = var.labels

  conditions {
    display_name = "Any kill switch event"
    condition_threshold {
      filter          = "metric.type=\"logging.googleapis.com/user/${google_logging_metric.kill_switch.name}\" resource.type=\"cloud_run_revision\""
      comparison      = "COMPARISON_GT"
      threshold_value = 0
      duration        = "0s"
      aggregations {
        alignment_period   = "60s"
        per_series_aligner = "ALIGN_SUM"
      }
    }
  }

  notification_channels = local.notification_channels
  alert_strategy {
    auto_close = "604800s"
  }
}

# ---------------------------------------------------------------------------
# Alert: Exchange API latency (custom metric written by market-data service)
# ---------------------------------------------------------------------------
resource "google_monitoring_alert_policy" "exchange_latency" {
  project      = var.project_id
  display_name = "Exchange API latency > ${var.exchange_latency_threshold_ms}ms (${var.environment})"
  combiner     = "OR"
  user_labels  = var.labels

  conditions {
    display_name = "p95 latency exceeded"
    condition_threshold {
      filter          = "metric.type=\"custom.googleapis.com/arb/exchange_latency_ms\" resource.type=\"cloud_run_revision\""
      comparison      = "COMPARISON_GT"
      threshold_value = var.exchange_latency_threshold_ms
      duration        = "180s"
      aggregations {
        alignment_period     = "60s"
        per_series_aligner   = "ALIGN_PERCENTILE_95"
        cross_series_reducer = "REDUCE_MAX"
        group_by_fields      = ["metric.label.exchange"]
      }
    }
  }

  notification_channels = local.notification_channels
  alert_strategy {
    auto_close = "1800s"
  }
}

# ---------------------------------------------------------------------------
# Alert: Daily loss > threshold (custom metric written by risk-engine)
# ---------------------------------------------------------------------------
resource "google_monitoring_alert_policy" "daily_loss" {
  project      = var.project_id
  display_name = "Daily loss > ${var.daily_loss_threshold_pct * 100}% (${var.environment})"
  combiner     = "OR"
  user_labels  = var.labels

  conditions {
    display_name = "Daily P&L fraction breach"
    condition_threshold {
      filter          = "metric.type=\"custom.googleapis.com/arb/daily_loss_pct\" resource.type=\"cloud_run_revision\""
      comparison      = "COMPARISON_GT"
      threshold_value = var.daily_loss_threshold_pct
      duration        = "60s"
      aggregations {
        alignment_period   = "60s"
        per_series_aligner = "ALIGN_MAX"
      }
    }
  }

  notification_channels = local.notification_channels
  alert_strategy {
    auto_close = "86400s"
  }
}
