terraform {
  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 6.0"
    }
  }
}

# ---------------------------------------------------------------------------
# Dedicated least-privilege service account per Cloud Run service
# ---------------------------------------------------------------------------
resource "google_service_account" "svc" {
  project      = var.project_id
  account_id   = "sa-${var.service_name}"
  display_name = "Cloud Run SA for ${var.service_name}"
}

# Publisher on the topics this service emits to
resource "google_pubsub_topic_iam_member" "publisher" {
  for_each = toset(var.publish_topics)

  project = var.project_id
  topic   = each.value
  role    = "roles/pubsub.publisher"
  member  = "serviceAccount:${google_service_account.svc.email}"
}

# Subscriber on each subscription this service consumes
resource "google_pubsub_subscription_iam_member" "subscriber" {
  for_each = toset(var.subscribe_subs)

  project      = var.project_id
  subscription = each.value
  role         = "roles/pubsub.subscriber"
  member       = "serviceAccount:${google_service_account.svc.email}"
}

# Secret-accessor IAM, scoped to ONLY the secrets this service mounts
resource "google_secret_manager_secret_iam_member" "accessor" {
  for_each = toset(var.secrets)

  project   = var.project_id
  secret_id = each.value
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.svc.email}"
}

# ---------------------------------------------------------------------------
# Cloud Run v2 service (gen2 execution environment)
# ---------------------------------------------------------------------------
resource "google_cloud_run_v2_service" "svc" {
  project  = var.project_id
  location = var.region
  name     = var.service_name
  ingress  = var.ingress
  labels   = var.labels

  # Cloud Run v2 defaults this to true. Our services hold no state (Redis +
  # BigQuery are the systems of record), so blocking replacement on every
  # config change adds friction without protecting data. Set false explicitly
  # so Terraform can destroy/recreate when image_tag or scaling changes
  # require it.
  deletion_protection = false

  template {
    service_account       = google_service_account.svc.email
    execution_environment = "EXECUTION_ENVIRONMENT_GEN2"
    labels                = var.labels

    scaling {
      min_instance_count = var.min_instances
      max_instance_count = var.max_instances
    }

    vpc_access {
      connector = var.vpc_connector
      egress    = "PRIVATE_RANGES_ONLY"
    }

    containers {
      image = var.image

      ports {
        container_port = var.container_port
      }

      resources {
        limits = {
          cpu    = var.cpu
          memory = var.memory
        }
        cpu_idle          = var.cpu_idle
        startup_cpu_boost = true
      }

      # Plain env vars
      dynamic "env" {
        for_each = var.env_vars
        content {
          name  = env.key
          value = env.value
        }
      }

      # Secret-backed env vars — name matches secret_id by convention
      dynamic "env" {
        for_each = toset(var.secrets)
        content {
          name = env.value
          value_source {
            secret_key_ref {
              secret  = env.value
              version = "latest"
            }
          }
        }
      }

      startup_probe {
        http_get {
          path = "/healthz"
          port = var.container_port
        }
        initial_delay_seconds = 5
        period_seconds        = 10
        timeout_seconds       = 3
        failure_threshold     = 6
      }

      liveness_probe {
        http_get {
          path = "/healthz"
          port = var.container_port
        }
        period_seconds    = 30
        timeout_seconds   = 5
        failure_threshold = 3
      }
    }
  }

  traffic {
    type    = "TRAFFIC_TARGET_ALLOCATION_TYPE_LATEST"
    percent = 100
  }

  depends_on = [
    google_pubsub_topic_iam_member.publisher,
    google_pubsub_subscription_iam_member.subscriber,
    google_secret_manager_secret_iam_member.accessor,
  ]
}

# ---------------------------------------------------------------------------
# Public invocation — only enabled for explicitly opt-in services (today,
# just dashboard-api). Default-deny everything else.
# ---------------------------------------------------------------------------
resource "google_cloud_run_v2_service_iam_member" "public_invoker" {
  count    = var.allow_public_invoke ? 1 : 0
  project  = var.project_id
  location = google_cloud_run_v2_service.svc.location
  name     = google_cloud_run_v2_service.svc.name
  role     = "roles/run.invoker"
  member   = "allUsers"
}

# ---------------------------------------------------------------------------
# BigQuery read access — granted at the project level because BQ data
# access has to be either project-wide or dataset-by-dataset, and our
# dataset list is short enough that project-wide reader is fine. dataViewer
# covers SELECT; jobUser is required to actually run a query.
# ---------------------------------------------------------------------------
resource "google_project_iam_member" "bigquery_dataviewer" {
  count   = var.bigquery_reader ? 1 : 0
  project = var.project_id
  role    = "roles/bigquery.dataViewer"
  member  = "serviceAccount:${google_service_account.svc.email}"
}

resource "google_project_iam_member" "bigquery_jobuser" {
  count   = var.bigquery_reader ? 1 : 0
  project = var.project_id
  role    = "roles/bigquery.jobUser"
  member  = "serviceAccount:${google_service_account.svc.email}"
}
