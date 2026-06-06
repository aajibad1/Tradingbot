variable "project_id" { type = string }
variable "region" { type = string }
variable "environment" { type = string }
variable "network_id" {
  type        = string
  description = "VPC network self_link/id for the private-IP connection."
}
variable "tier" {
  type    = string
  default = "db-custom-1-3840" # 1 vCPU / 3.75GB; bump for prod load
}
variable "database_name" {
  type    = string
  default = "platform"
}
variable "user_name" {
  type    = string
  default = "platform"
}
variable "labels" {
  type    = map(string)
  default = {}
}
