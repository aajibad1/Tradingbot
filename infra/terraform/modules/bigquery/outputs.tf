output "dataset_ids" {
  description = "Map of dataset_id => fully-qualified BQ dataset reference."
  value       = { for k, d in google_bigquery_dataset.dataset : k => d.id }
}

output "table_ids" {
  description = "Map of table_id => fully-qualified BQ table reference."
  value       = { for k, t in google_bigquery_table.table : k => t.id }
}
