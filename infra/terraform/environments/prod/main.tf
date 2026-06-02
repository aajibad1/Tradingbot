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
    prefix = "tf-state/crypto-arb/prod"
  }
}

module "root" {
  source = "../../"

  project_id  = var.project_id
  region      = var.region
  environment = "prod"

  # Production footprint — keep WebSocket collectors warm
  cloud_run_min_instances = 1
  cloud_run_max_instances = 10
  redis_memory_size_gb    = 1
  redis_tier              = "BASIC"

  image_tag                = var.image_tag
  enable_monitoring_alerts = true
}

variable "project_id" {
  description = <<-EOD
    GCP project for the PROD environment. MUST be a different project from dev
    (datasets/topics/secrets are not env-suffixed, so a shared project collides).
  EOD
  type        = string
  default     = "agenuit"
}

variable "region" {
  type    = string
  default = "us-central1"
}

variable "image_tag" {
  description = <<-EOD
    Docker image tag to deploy to every Cloud Run service. CI overrides this
    with the git SHA from the build-and-push job. Defaults to "latest" so
    manual plans/applies and ad-hoc rollbacks don't have to hand-pick a SHA;
    the `latest` tag is set on every successful CI build.
  EOD
  type        = string
  default     = "latest"
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
