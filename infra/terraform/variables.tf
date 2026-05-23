variable "project_id" {
  description = "GCP project ID hosting the crypto arbitrage system."
  type        = string
  default     = "agenuit"
}

variable "region" {
  description = "Default GCP region for regional resources."
  type        = string
  default     = "us-central1"
}

variable "zone" {
  description = "Default GCP zone for zonal resources (Memorystore)."
  type        = string
  default     = "us-central1-a"
}

variable "environment" {
  description = "Deployment environment label (dev, staging, prod)."
  type        = string
  default     = "dev"

  validation {
    condition     = contains(["dev", "staging", "prod"], var.environment)
    error_message = "environment must be one of dev | staging | prod."
  }
}

variable "image_tag" {
  description = "Container image tag (git SHA or semver) applied to every Cloud Run service."
  type        = string
  default     = "latest"
}

variable "artifact_registry_repo" {
  description = "Artifact Registry repository hosting service images."
  type        = string
  default     = "crypto-arb"
}

variable "redis_tier" {
  description = "Memorystore Redis service tier."
  type        = string
  default     = "BASIC"
}

variable "redis_memory_size_gb" {
  description = "Memorystore Redis memory size in GiB."
  type        = number
  default     = 1
}

variable "cloud_run_min_instances" {
  description = "Minimum Cloud Run instance count (1 keeps WebSocket collectors warm)."
  type        = number
  default     = 1
}

variable "cloud_run_max_instances" {
  description = "Maximum Cloud Run instance count."
  type        = number
  default     = 5
}

variable "slack_webhook_secret_id" {
  description = "Secret Manager ID containing the Slack incoming webhook URL used for alerting."
  type        = string
  default     = "SLACK_WEBHOOK_URL"
}

variable "enable_monitoring_alerts" {
  description = "Toggle monitoring alert policies (disabled in dev to avoid noise)."
  type        = bool
  default     = false
}

variable "labels" {
  description = "Common labels applied to every taggable resource."
  type        = map(string)
  default = {
    project = "arb-system"
  }
}
