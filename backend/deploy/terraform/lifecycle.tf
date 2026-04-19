# ============================================================================
# S3 lifecycle rules sul bucket backup
# ============================================================================
# Tre prefissi, tre policy diverse:
#
#   plenora_*             Dump DB daily. Standard -> Standard-IA dopo 30gg
#                         -> delete dopo 365gg. Restore e' raro, IA ha costo
#                         storage piu' basso e costo retrieval accettabile.
#
#   eseguibili-outputs/   Output workflow utente. Eager cleanup post-download
#                         lato codice. Safety net: delete dopo 7gg per
#                         oggetti orfani.
#
#   imdf-bundles/         Bundle IMDF generati. Stessa policy effimera.
#
# `filter { prefix = ... }` matcha oggetti, `filter {}` vuoto matcha tutto.

resource "aws_s3_bucket_lifecycle_configuration" "backup" {
  bucket = var.backup_bucket_name

  rule {
    id     = "db-backup-dumps"
    status = "Enabled"

    filter {
      prefix = "plenora_"
    }

    transition {
      days          = var.db_backup_ia_transition_days
      storage_class = "STANDARD_IA"
    }

    expiration {
      days = var.db_backup_retention_days
    }

    # Cleanup upload multipart abbandonati (sanita' generale)
    abort_incomplete_multipart_upload {
      days_after_initiation = 7
    }
  }

  rule {
    id     = "eseguibili-outputs-cleanup"
    status = "Enabled"

    filter {
      prefix = "eseguibili-outputs/"
    }

    expiration {
      days = var.ephemeral_retention_days
    }

    abort_incomplete_multipart_upload {
      days_after_initiation = 1
    }
  }

  rule {
    id     = "imdf-bundles-cleanup"
    status = "Enabled"

    filter {
      prefix = "imdf-bundles/"
    }

    expiration {
      days = var.ephemeral_retention_days
    }

    abort_incomplete_multipart_upload {
      days_after_initiation = 1
    }
  }
}
