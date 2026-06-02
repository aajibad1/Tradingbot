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

  authorized_network = var.network_id
  connect_mode       = "DIRECT_PEERING"

  # Security: the risk-engine's entire kill-switch / capital / exposure state
  # lives here. Encrypt it in transit and require AUTH so it is not readable or
  # writable by anything on the VPC without the password. Clients connect with
  # rediss:// + the auth_string (wired into REDIS_URL by the root module).
  transit_encryption_mode = var.transit_encryption_mode
  auth_enabled            = var.auth_enabled

  display_name = "Risk-engine Redis state store"
  labels       = var.labels
}
