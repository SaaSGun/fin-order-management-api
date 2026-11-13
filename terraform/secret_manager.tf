# Holds the *full* DATABASE_URL (not just the raw password) so Cloud Run
# can source the env var straight from Secret Manager via secret_key_ref —
# the plaintext connection string never appears in the Cloud Run service
# spec itself, only a reference to this secret's latest version.

resource "google_secret_manager_secret" "database_url" {
  secret_id = "${var.service_name}-database-url"

  replication {
    auto {}
  }

  depends_on = [google_project_service.required]
}

resource "google_secret_manager_secret_version" "database_url" {
  secret = google_secret_manager_secret.database_url.id
  secret_data = join("", [
    "postgresql+asyncpg://",
    var.db_user, ":", random_password.db_password.result,
    "@/", var.db_name,
    "?host=/cloudsql/", google_sql_database_instance.main.connection_name,
  ])
}

# Separate secret holding just the raw password, so the GitHub Actions
# deploy workflow can build a *TCP* connection string for the Cloud SQL
# Auth Proxy tunnel (migrations run from the runner, which has no
# `/cloudsql` socket) without parsing it out of the composed URL above.
resource "google_secret_manager_secret" "db_password" {
  secret_id = "${var.service_name}-db-password"

  replication {
    auto {}
  }

  depends_on = [google_project_service.required]
}

resource "google_secret_manager_secret_version" "db_password" {
  secret      = google_secret_manager_secret.db_password.id
  secret_data = random_password.db_password.result
}
