output "host" {
  value       = google_redis_instance.redis.host
  description = "Redis private IP (VPC-internal)."
}

output "port" {
  value = google_redis_instance.redis.port
}

output "id" {
  value = google_redis_instance.redis.id
}

output "auth_string" {
  description = "Generated AUTH password (empty when auth_enabled = false)."
  value       = google_redis_instance.redis.auth_string
  sensitive   = true
}

output "server_ca_certs" {
  description = "Server CA cert(s) for TLS verification (when transit encryption is on)."
  value       = google_redis_instance.redis.server_ca_certs
  sensitive   = true
}
