variable "project_id" {
  type = string
}

variable "environment" {
  type = string
}

variable "slack_webhook_secret_id" {
  description = <<-EOD
    Secret Manager ID of the Slack incoming webhook URL.

    NOTE: Only consumed when ``enable_slack_alert_channel = true``, which in
    turn requires that the secret hold a Slack App OAuth bot token
    (``xoxb-...``) — NOT a plain webhook URL. GCP Cloud Monitoring's native
    Slack channel type validates with Slack's OAuth API and rejects
    webhooks.

    Runtime services post directly to the webhook URL via the
    SLACK_WEBHOOK_URL env var (see services/risk-engine/rules/kill_switch.py
    and services/execution-orchestrator/approval_gate.py). That path is
    unaffected by this module's Slack channel toggle.
  EOD
  type        = string
}

variable "enable_slack_alert_channel" {
  description = <<-EOD
    When true, create a GCP Cloud Monitoring Slack notification channel
    using the secret named by ``slack_webhook_secret_id``. The secret value
    must be a Slack App OAuth bot token. Defaults to false because the
    secret most projects start with is an incoming webhook URL, which GCP's
    Slack channel type rejects.
  EOD
  type        = bool
  default     = false
}

variable "service_names" {
  description = "Cloud Run service names monitored for 5xx error rate."
  type        = list(string)
}

variable "subscription_names" {
  description = "Pub/Sub subscriptions monitored for oldest_unacked_message_age."
  type        = list(string)
}

variable "labels" {
  type    = map(string)
  default = {}
}

variable "cloud_run_error_rate_threshold" {
  description = "Fraction (0..1). Default 0.01 = 1% 5xx."
  type        = number
  default     = 0.01
}

variable "subscription_age_threshold_seconds" {
  type    = number
  default = 60
}

variable "exchange_latency_threshold_ms" {
  type    = number
  default = 500
}

variable "daily_loss_threshold_pct" {
  description = "Fraction (0..1). Default 0.008 = 0.8% daily loss."
  type        = number
  default     = 0.008
}
