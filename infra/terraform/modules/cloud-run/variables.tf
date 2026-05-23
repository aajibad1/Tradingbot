variable "project_id" {
  type = string
}

variable "region" {
  type = string
}

variable "service_name" {
  description = "Service name (matches the services/<name>/ directory)."
  type        = string
}

variable "image" {
  description = "Fully-qualified container image, e.g. us-central1-docker.pkg.dev/agenuit/crypto-arb/<svc>:<tag>."
  type        = string
}

variable "env_vars" {
  description = "Plain (non-secret) environment variables injected into the container."
  type        = map(string)
  default     = {}
}

variable "secrets" {
  description = "Secret Manager IDs mounted as env vars (same name as the secret ID)."
  type        = list(string)
  default     = []
}

variable "publish_topics" {
  description = "Pub/Sub topic names this service may PUBLISH to."
  type        = list(string)
  default     = []
}

variable "subscribe_subs" {
  description = "Pub/Sub subscription names this service may CONSUME from."
  type        = list(string)
  default     = []
}

variable "vpc_connector" {
  description = "Serverless VPC Access connector ID."
  type        = string
}

variable "min_instances" {
  type    = number
  default = 1
}

variable "max_instances" {
  type    = number
  default = 5
}

variable "cpu" {
  type    = string
  default = "1"
}

variable "memory" {
  type    = string
  default = "512Mi"
}

variable "cpu_idle" {
  description = "If false, CPU is always allocated (required for WebSocket-pumping services)."
  type        = bool
  default     = false
}

variable "container_port" {
  type    = number
  default = 8080
}

variable "labels" {
  type    = map(string)
  default = {}
}

variable "allow_public_invoke" {
  description = <<-EOD
    Grant ``allUsers`` ``roles/run.invoker``. Use sparingly — only the
    dashboard-api currently needs to be reachable from a browser without
    an ID token. Defaults to false so every other service stays
    authenticated.
  EOD
  type        = bool
  default     = false
}

variable "bigquery_reader" {
  description = <<-EOD
    Grant the service's runtime SA ``roles/bigquery.dataViewer`` +
    ``roles/bigquery.jobUser`` so it can query the project's datasets.
    Only the dashboard-api needs this today.
  EOD
  type        = bool
  default     = false
}
