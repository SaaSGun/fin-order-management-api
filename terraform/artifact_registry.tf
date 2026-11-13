resource "google_artifact_registry_repository" "app" {
  location      = var.region
  repository_id = var.service_name
  format        = "DOCKER"
  description   = "fin-oms-api container images"

  depends_on = [google_project_service.required]
}
