variable "aws_region" {
  description = "Regione AWS dove vive il bucket target."
  type        = string
  default     = "eu-south-1"
}

variable "backup_bucket_name" {
  description = <<-EOT
    Nome del bucket S3 che ospita backup, eseguibili-outputs e imdf-bundles.
    Deve esistere gia' (creato manualmente o via altra stack TF).
  EOT
  type        = string
  default     = "plenora-backup-112011134642-eu-south-1-an"
}

variable "db_backup_retention_days" {
  description = "Giorni prima di delete permanente per i dump DB (prefix 'urby_')."
  type        = number
  default     = 365
}

variable "db_backup_ia_transition_days" {
  description = <<-EOT
    Giorni prima di transizione a Standard-IA per i dump DB. Riduce costi
    per backup non acceduti (restore e' raro). Min AWS: 30 giorni.
  EOT
  type        = number
  default     = 30
  validation {
    condition     = var.db_backup_ia_transition_days >= 30
    error_message = "AWS richiede almeno 30 giorni prima della transizione a Standard-IA."
  }
}

variable "ephemeral_retention_days" {
  description = <<-EOT
    Giorni prima di delete per oggetti effimeri (eseguibili-outputs/,
    imdf-bundles/). Il codice ha gia' eager-cleanup post-download; questa
    regola e' safety net per orfani (es. download fallito, process killed).
  EOT
  type        = number
  default     = 7
  validation {
    condition     = var.ephemeral_retention_days >= 1
    error_message = "TTL minimo 1 giorno (AWS lifecycle granularity)."
  }
}
