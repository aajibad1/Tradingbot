output "service_url" {
  description = "HTTPS URL of the Cloud Run service."
  value       = google_cloud_run_v2_service.svc.uri
}

output "service_account_email" {
  description = "Email of the dedicated service account."
  value       = google_service_account.svc.email
}

output "service_name" {
  value = google_cloud_run_v2_service.svc.name
}
