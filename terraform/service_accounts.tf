# Runtime identity: what the deployed container itself runs as.
resource "google_service_account" "run_runtime" {
  account_id   = "${var.service_name}-run"
  display_name = "fin-oms-api Cloud Run runtime"
}

resource "google_project_iam_member" "run_cloudsql_client" {
  project = var.project_id
  role    = "roles/cloudsql.client"
  member  = "serviceAccount:${google_service_account.run_runtime.email}"
}

resource "google_secret_manager_secret_iam_member" "run_secret_access" {
  secret_id = google_secret_manager_secret.database_url.id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.run_runtime.email}"
}

# Deploy identity: what GitHub Actions impersonates via WIF (no key file).
resource "google_service_account" "deployer" {
  account_id   = "${var.service_name}-deploy"
  display_name = "fin-oms-api GitHub Actions deployer"
}

resource "google_project_iam_member" "deployer_run_admin" {
  project = var.project_id
  role    = "roles/run.admin"
  member  = "serviceAccount:${google_service_account.deployer.email}"
}

resource "google_project_iam_member" "deployer_artifact_writer" {
  project = var.project_id
  role    = "roles/artifactregistry.writer"
  member  = "serviceAccount:${google_service_account.deployer.email}"
}

resource "google_project_iam_member" "deployer_cloudsql_client" {
  project = var.project_id
  role    = "roles/cloudsql.client"
  member  = "serviceAccount:${google_service_account.deployer.email}"
}

# Needed to build a TCP connection string for the Cloud SQL Auth Proxy
# tunnel when running migrations from the GitHub Actions runner.
resource "google_secret_manager_secret_iam_member" "deployer_db_password_access" {
  secret_id = google_secret_manager_secret.db_password.id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.deployer.email}"
}

# Lets the deployer attach/act as the runtime SA when deploying revisions.
resource "google_service_account_iam_member" "deployer_act_as_runtime" {
  service_account_id = google_service_account.run_runtime.name
  role                = "roles/iam.serviceAccountUser"
  member              = "serviceAccount:${google_service_account.deployer.email}"
}
