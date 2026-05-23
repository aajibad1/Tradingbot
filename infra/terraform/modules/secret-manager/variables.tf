variable "project_id" {
  type = string
}

variable "secret_ids" {
  description = "Secret IDs to create. Values must be populated out-of-band; this module never writes payloads."
  type        = list(string)
}

variable "labels" {
  type    = map(string)
  default = {}
}
