# Cloud SQL Postgres — the control-plane system-of-record (identity, tenancy,
# RBAC, onboarding, billing, audit) for core-api + the internal ledger for
# accounts-service. Private IP only; reached from Cloud Run over the VPC connector.

terraform {
  required_providers {
    google = { source = "hashicorp/google" }
    random = { source = "hashicorp/random" }
  }
}

# Reserve a private range and peer it to Google's service-networking so the
# instance can get a private IP on our VPC.
resource "google_compute_global_address" "private_range" {
  name          = "arb-sql-priv-${var.environment}"
  purpose       = "VPC_PEERING"
  address_type  = "INTERNAL"
  prefix_length = 16
  network       = var.network_id
}

resource "google_service_networking_connection" "private_vpc" {
  network                 = var.network_id
  service                 = "servicenetworking.googleapis.com"
  reserved_peering_ranges = [google_compute_global_address.private_range.name]
}

resource "random_password" "db" {
  length  = 28
  special = false # keep the password URL-safe for DATABASE_URL
}

resource "google_sql_database_instance" "pg" {
  name                = "arb-pg-${var.environment}"
  database_version    = "POSTGRES_15"
  region              = var.region
  deletion_protection = true

  settings {
    tier              = var.tier
    availability_type = "ZONAL"
    disk_autoresize   = true
    user_labels       = var.labels

    ip_configuration {
      ipv4_enabled    = false
      private_network = var.network_id
    }
    backup_configuration {
      enabled                        = true
      point_in_time_recovery_enabled = true
    }
  }

  depends_on = [google_service_networking_connection.private_vpc]
}

resource "google_sql_database" "db" {
  name     = var.database_name
  instance = google_sql_database_instance.pg.name
}

resource "google_sql_user" "user" {
  name     = var.user_name
  instance = google_sql_database_instance.pg.name
  password = random_password.db.result
}
