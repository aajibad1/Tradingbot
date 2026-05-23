resource "google_redis_instance" "arb_cache" {
  name           = "arb-redis"
  tier           = "BASIC"
  memory_size_gb = 1
  region         = var.region
  project        = var.project_id
  redis_version  = "REDIS_7_0"

  labels = {
    environment = "production"
    system      = "crypto-arb"
  }
}

output "redis_host" {
  value = google_redis_instance.arb_cache.host
}

output "redis_port" {
  value = google_redis_instance.arb_cache.port
}
