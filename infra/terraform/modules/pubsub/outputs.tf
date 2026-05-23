output "topic_ids" {
  description = "Map of topic name => fully-qualified topic ID."
  value       = { for k, t in google_pubsub_topic.topic : k => t.id }
}

output "dead_letter_topic_ids" {
  value = { for k, t in google_pubsub_topic.dead_letter : k => t.id }
}

output "subscription_ids" {
  description = "Map of subscription name => fully-qualified subscription ID."
  value       = { for k, s in google_pubsub_subscription.sub : k => s.id }
}
