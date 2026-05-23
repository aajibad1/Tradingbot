variable "project_id" {
  type = string
}

variable "topics" {
  description = "List of Pub/Sub topic names to create. Mirror of Topic enum in shared/pubsub/publisher.py."
  type        = list(string)
}

variable "subscriptions" {
  description = "Map of subscription_name => topic_name. Convention: <topic>-<consumer>."
  type        = map(string)
}

variable "labels" {
  type    = map(string)
  default = {}
}

variable "ack_deadline_seconds" {
  type    = number
  default = 30
}

variable "message_retention_duration" {
  description = "Topic message retention (Pub/Sub default is 7 days)."
  type        = string
  default     = "604800s"
}

variable "dead_letter_max_attempts" {
  type    = number
  default = 5
}
