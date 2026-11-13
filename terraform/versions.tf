terraform {
  required_version = ">= 1.6"

  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 6.0"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.6"
    }
  }

  # `terraform init -backend-config="bucket=<your-state-bucket>"` — the
  # bucket itself is created manually before the first init (see README).
  backend "gcs" {
    prefix = "fin-oms-api/state"
  }
}

provider "google" {
  project = var.project_id
  region  = var.region
}
