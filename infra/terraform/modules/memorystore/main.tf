terraform {
  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 6.0"
    }
  }
}

resource "google_redis_instance" "redis" {
  project        = var.project_id
  region         = var.region
  name           = var.name
  tier           = var.tier
  memory_size_gb = var.memory_size_gb
  redis_version  = var.redis_version

  authorized_network      = var.network_id
  connect_mode            = "DIRECT_PEERING"
  transit_encryption_mode = "DISABLED"

  display_name = "Risk-engine Redis state store"
  labels       = var.labels
}
