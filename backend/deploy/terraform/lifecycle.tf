# ============================================================================
# S3 lifecycle rules sui bucket backup e runtime
# ============================================================================
# Backup e artifact runtime usano bucket e policy distinte:
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

}

# Runtime objects live in a bucket distinct from administrative backups. New
# tenant-safe keys start with tenants/{group}/..., so a domain prefix cannot
# match every tenant. Ephemeral producers tag their objects explicitly.
resource "aws_s3_bucket_lifecycle_configuration" "runtime" {
  bucket = var.runtime_bucket_name

  rule {
    id     = "tagged-ephemeral-cleanup"
    status = "Enabled"

    filter {
      tag {
        key   = "plenora-retention"
        value = "ephemeral"
      }
    }

    expiration {
      days = var.ephemeral_retention_days
    }
  }

  rule {
    id     = "tenant-incomplete-multipart-cleanup"
    status = "Enabled"

    filter {
      prefix = "tenants/"
    }

    abort_incomplete_multipart_upload {
      days_after_initiation = 1
    }
  }

  # Compatibility safety nets for objects written before tenant namespacing.
  dynamic "rule" {
    for_each = toset(["eseguibili-outputs", "imdf-bundles"])
    content {
      id     = "${rule.value}-legacy-cleanup"
      status = "Enabled"
      filter {
        prefix = "${rule.value}/"
      }
      expiration {
        days = var.ephemeral_retention_days
      }
      abort_incomplete_multipart_upload {
        days_after_initiation = 1
      }
    }
  }
}
