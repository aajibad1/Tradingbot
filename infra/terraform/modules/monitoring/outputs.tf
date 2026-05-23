output "notification_channel_id" {
  value = google_monitoring_notification_channel.slack.id
}

output "alert_policy_ids" {
  value = [
    google_monitoring_alert_policy.cloud_run_error_rate.id,
    google_monitoring_alert_policy.pubsub_backlog.id,
    google_monitoring_alert_policy.kill_switch.id,
    google_monitoring_alert_policy.exchange_latency.id,
    google_monitoring_alert_policy.daily_loss.id,
  ]
}
