output "private_ip" {
  value = google_sql_database_instance.pg.private_ip_address
}

output "connection_name" {
  value = google_sql_database_instance.pg.connection_name
}

# SQLAlchemy/psycopg3 URL over the private IP (Cloud Run reaches it via the VPC
# connector). Sensitive — embeds the generated password.
output "database_url" {
  value     = "postgresql+psycopg://${var.user_name}:${random_password.db.result}@${google_sql_database_instance.pg.private_ip_address}:5432/${var.database_name}"
  sensitive = true
}
