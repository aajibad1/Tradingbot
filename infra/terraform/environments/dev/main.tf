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

  backend "gcs" {
    # bucket configured via `terraform init -backend-config=bucket=...`
    prefix = "tf-state/crypto-arb/dev"
  }
}

module "root" {
  source = "../../"

  project_id  = var.project_id
  region      = var.region
  environment = "dev"

  # Smaller footprint in dev
  cloud_run_min_instances = 0
  cloud_run_max_instances = 2
  redis_memory_size_gb    = 1
  redis_tier              = "BASIC"

  image_tag                = var.image_tag
  enable_monitoring_alerts = false
}

variable "project_id" {
  description = <<-EOD
    GCP project for the DEV environment. MUST be a different project from prod —
    BigQuery datasets, Pub/Sub topics, and secrets are not env-suffixed, so a
    shared project would let a dev apply clobber prod's data. Override with
    -var project_id=... to match your actual dev project.
  EOD
  type        = string
  default     = "agenuit-dev"
}

variable "region" {
  type    = string
  default = "us-central1"
}

variable "image_tag" {
  type    = string
  default = "latest"
}

output "cloud_run_service_urls" {
  value = module.root.cloud_run_service_urls
}

output "pubsub_topics" {
  value = module.root.pubsub_topics
}

output "bigquery_datasets" {
  value = module.root.bigquery_datasets
}

output "redis_host" {
  value = module.root.redis_host
}
