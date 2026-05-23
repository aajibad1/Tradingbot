output "cloud_run_service_urls" {
  description = "Public URL for each Cloud Run service, keyed by service name."
  value       = { for k, m in module.cloud_run : k => m.service_url }
}

output "cloud_run_service_accounts" {
  description = "Service account email for each Cloud Run service."
  value       = { for k, m in module.cloud_run : k => m.service_account_email }
}

output "pubsub_topics" {
  description = "Fully-qualified topic IDs."
  value       = module.pubsub.topic_ids
}

output "pubsub_subscriptions" {
  description = "Fully-qualified subscription IDs."
  value       = module.pubsub.subscription_ids
}

output "bigquery_datasets" {
  description = "BigQuery dataset IDs."
  value       = module.bigquery.dataset_ids
}

output "redis_host" {
  description = "Memorystore Redis host (VPC-only)."
  value       = module.memorystore.host
}

output "redis_port" {
  description = "Memorystore Redis port."
  value       = module.memorystore.port
}

output "vpc_connector_id" {
  description = "Serverless VPC Access connector ID for Cloud Run private networking."
  value       = google_vpc_access_connector.connector.id
}

output "artifact_registry_repo" {
  description = "Artifact Registry repo URL prefix; append `<service>:<tag>` to form an image ref."
  value       = local.artifact_registry_prefix
}
