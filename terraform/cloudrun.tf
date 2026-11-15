resource "google_cloud_run_v2_service" "app" {
  name     = var.service_name
  location = var.region
  ingress  = "INGRESS_TRAFFIC_ALL"

  template {
    service_account = google_service_account.run_runtime.email

    # Connection budget. db-f1-micro allows 25 connections, 3 reserved for
    # superusers -> 22. 3 instances x 6 pooled connections = 18, leaving 4
    # for CI migrations and admin sessions through the Auth Proxy. Unbounded,
    # 3 x (5 + 10 overflow) = 45 exhausted the database under load (README,
    # "Load testing"). Raise both together if the tier changes.
    scaling {
      max_instance_count = 3
    }

    containers {
      # Placeholder for the first `terraform apply`, before any image has
      # been pushed to Artifact Registry. CI/CD owns the image after that
      # (see `lifecycle.ignore_changes` below) — this value is never read
      # again once deploy.yml has run once.
      image = "us-docker.pkg.dev/cloudrun/container/hello"

      ports {
        container_port = 8080
      }

      env {
        name = "DATABASE_URL"
        value_source {
          secret_key_ref {
            secret  = google_secret_manager_secret.database_url.secret_id
            version = "latest"
          }
        }
      }

      # Fixed pool, no overflow: the instance never opens more than its share
      # of the budget above. A request that waits DB_POOL_TIMEOUT seconds for
      # a connection gets a 503 + Retry-After (app/observability.py).
      env {
        name  = "DB_POOL_SIZE"
        value = "6"
      }
      env {
        name  = "DB_MAX_OVERFLOW"
        value = "0"
      }
      env {
        name  = "DB_POOL_TIMEOUT"
        value = "10"
      }

      volume_mounts {
        name       = "cloudsql"
        mount_path = "/cloudsql"
      }
    }

    volumes {
      name = "cloudsql"
      cloud_sql_instance {
        instances = [google_sql_database_instance.main.connection_name]
      }
    }
  }

  lifecycle {
    ignore_changes = [
      template[0].containers[0].image,
    ]
  }

  depends_on = [
    google_project_service.required,
    google_secret_manager_secret_iam_member.run_secret_access,
  ]
}

resource "google_cloud_run_v2_service_iam_member" "public_invoker" {
  name     = google_cloud_run_v2_service.app.name
  location = var.region
  role     = "roles/run.invoker"
  member   = "allUsers"
}
