terraform {
  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 6.0"
    }
  }
}

# Create the secret containers. NEVER store the value in Terraform state.
# Values are populated via:
#   echo -n "$KEY" | gcloud secrets versions add <SECRET_ID> --data-file=-
#
# These are READ + TRADE keys only. Exchange keys MUST be issued without
# withdrawal permission at the venue — terraform cannot enforce that, so the
# operator runbook owns it.
resource "google_secret_manager_secret" "secret" {
  for_each = toset(var.secret_ids)

  project   = var.project_id
  secret_id = each.value
  labels    = var.labels

  replication {
    auto {}
  }
}
