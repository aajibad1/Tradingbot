variable "project_id" {
  type = string
}

variable "environment" {
  type = string
}

variable "slack_webhook_secret_id" {
  description = "Secret Manager ID of the Slack incoming webhook URL."
  type        = string
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
