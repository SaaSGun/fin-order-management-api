variable "project_id" {
  type        = string
  description = "Dedicated GCP project for fin-oms-api (created manually, see README)."
}

variable "region" {
  type        = string
  description = "GCP region — europe-west4, mirroring the fin-analytics-api sibling repo."
  default     = "europe-west4"
}

variable "github_repo" {
  type        = string
  description = "GitHub repo (owner/name) trusted by Workload Identity Federation for deploys."
  default     = "SaaSGun/fin-order-management-api"
}

variable "service_name" {
  type        = string
  description = "Base name used for the Cloud Run service and related resources."
  default     = "fin-oms-api"
}

variable "db_tier" {
  type        = string
  description = "Cloud SQL machine tier. Shared-core (no SLA) is an accepted tradeoff for a portfolio project."
  default     = "db-f1-micro"
}

variable "db_name" {
  type    = string
  default = "fin_oms"
}

variable "db_user" {
  type    = string
  default = "fin_oms"
}
