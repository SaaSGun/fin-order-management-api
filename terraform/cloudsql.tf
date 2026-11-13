# Always-on by design (see .claude/context.md) — a reviewer's link should
# work at any time. Shared-core tier keeps that cost modest (~$8-10/month
# on-demand) in exchange for no Cloud SQL SLA, an accepted tradeoff for a
# portfolio project, not a production system.

resource "random_password" "db_password" {
  length  = 24
  special = false # keep it URL-safe — it's embedded in a connection string
}

resource "google_sql_database_instance" "main" {
  name             = "${var.service_name}-pg"
  database_version = "POSTGRES_16"
  region           = var.region

  settings {
    tier              = var.db_tier
    # Shared-core tiers like db-f1-micro only exist under the Enterprise
    # edition; without this, new projects can default to Enterprise Plus,
    # which rejects db-f1-micro outright.
    edition           = "ENTERPRISE"
    availability_type = "ZONAL"

    backup_configuration {
      enabled = true
    }
  }

  # Prevents `terraform destroy` from silently dropping the always-on
  # database; remove explicitly (and intentionally) if this is ever torn
  # down for real.
  deletion_protection = true

  depends_on = [google_project_service.required]
}

resource "google_sql_database" "fin_oms" {
  name     = var.db_name
  instance = google_sql_database_instance.main.name
}

resource "google_sql_user" "fin_oms" {
  name     = var.db_user
  instance = google_sql_database_instance.main.name
  password = random_password.db_password.result
}
