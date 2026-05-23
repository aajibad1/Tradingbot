terraform {
  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 6.0"
    }
  }
}

# ---------------------------------------------------------------------------
# Topics (canonical + matching dead-letter topics)
# ---------------------------------------------------------------------------
resource "google_pubsub_topic" "topic" {
  for_each = toset(var.topics)

  project                    = var.project_id
  name                       = each.value
  labels                     = var.labels
  message_retention_duration = var.message_retention_duration
}

resource "google_pubsub_topic" "dead_letter" {
  for_each = toset(var.topics)

  project = var.project_id
  name    = "${each.value}-dead-letter"
  labels  = merge(var.labels, { role = "dead-letter" })
}

# ---------------------------------------------------------------------------
# Subscriptions (per-consumer; the topic each binds to comes from var.subscriptions)
# ---------------------------------------------------------------------------
resource "google_pubsub_subscription" "sub" {
  for_each = var.subscriptions

  project = var.project_id
  name    = each.key
  topic   = google_pubsub_topic.topic[each.value].id
  labels  = var.labels

  ack_deadline_seconds       = var.ack_deadline_seconds
  message_retention_duration = var.message_retention_duration
  enable_message_ordering    = false

  expiration_policy {
    ttl = "" # never expire
  }

  retry_policy {
    minimum_backoff = "10s"
    maximum_backoff = "600s"
  }

  dead_letter_policy {
    dead_letter_topic     = google_pubsub_topic.dead_letter[each.value].id
    max_delivery_attempts = var.dead_letter_max_attempts
  }
}

# Pub/Sub service account must be allowed to publish to the dead-letter topics
# and acknowledge messages on the live subscriptions. See:
# https://cloud.google.com/pubsub/docs/handling-failures#dead_letter_topic
data "google_project" "this" {
  project_id = var.project_id
}

locals {
  pubsub_sa = "serviceAccount:service-${data.google_project.this.number}@gcp-sa-pubsub.iam.gserviceaccount.com"
}

resource "google_pubsub_topic_iam_member" "dlq_publisher" {
  for_each = toset(var.topics)

  project = var.project_id
  topic   = google_pubsub_topic.dead_letter[each.value].name
  role    = "roles/pubsub.publisher"
  member  = local.pubsub_sa
}

resource "google_pubsub_subscription_iam_member" "dlq_subscriber" {
  for_each = var.subscriptions

  project      = var.project_id
  subscription = google_pubsub_subscription.sub[each.key].name
  role         = "roles/pubsub.subscriber"
  member       = local.pubsub_sa
}
