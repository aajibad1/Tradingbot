variable "project_id" {
  type = string
}

variable "region" {
  description = "BigQuery dataset location."
  type        = string
  default     = "US"
}

variable "datasets" {
  description = "Map of dataset_id => { description, default_table_expiry_days }. 0 days = no expiration."
  type = map(object({
    description               = string
    default_table_expiry_days = number
  }))
}

variable "labels" {
  type    = map(string)
  default = {}
}
