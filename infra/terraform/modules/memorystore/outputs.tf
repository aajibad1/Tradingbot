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
