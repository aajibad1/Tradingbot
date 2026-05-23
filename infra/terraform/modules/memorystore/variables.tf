variable "project_id" {
  type = string
}

variable "region" {
  type = string
}

variable "name" {
  type = string
}

variable "tier" {
  type    = string
  default = "BASIC"
}

variable "memory_size_gb" {
  type    = number
  default = 1
}

variable "redis_version" {
  type    = string
  default = "REDIS_7_0"
}

variable "network_id" {
  description = "Full VPC network ID (used for AUTHORIZED_NETWORK private access)."
  type        = string
}

variable "labels" {
  type    = map(string)
  default = {}
}
