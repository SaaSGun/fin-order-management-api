output "cloud_run_url" {
  value = google_cloud_run_v2_service.app.uri
}

output "artifact_registry_repository" {
  value = "${var.region}-docker.pkg.dev/${var.project_id}/${google_artifact_registry_repository.app.repository_id}"
}

output "cloudsql_connection_name" {
  value = google_sql_database_instance.main.connection_name
}

output "deployer_service_account_email" {
  description = "Set as GCP_DEPLOYER_SA in GitHub Actions secrets/variables."
  value       = google_service_account.deployer.email
}

output "workload_identity_provider" {
  description = "Set as GCP_WORKLOAD_IDENTITY_PROVIDER in GitHub Actions secrets/variables."
  value       = google_iam_workload_identity_pool_provider.github.name
}
