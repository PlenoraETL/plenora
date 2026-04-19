output "bucket" {
  description = "Bucket S3 su cui sono applicate le lifecycle rule."
  value       = aws_s3_bucket_lifecycle_configuration.backup.bucket
}

output "rule_ids" {
  description = "ID delle lifecycle rule gestite da Terraform."
  value       = [for r in aws_s3_bucket_lifecycle_configuration.backup.rule : r.id]
}
