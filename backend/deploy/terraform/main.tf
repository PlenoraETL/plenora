# ============================================================================
# Provider + backend config
# ============================================================================
# State su S3 con lock DynamoDB. Bucket + tabella creati una tantum dal
# bootstrap script (vedi bootstrap/README.md) — questo blocco assume che
# esistano gia'.
#
# Nota: il `backend "s3"` block NON accetta variabili Terraform (limit del
# linguaggio). Se vuoi customizzare bucket/region, modificali qui o passa
# `-backend-config=...` a `terraform init`.

terraform {
  required_version = ">= 1.5.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }

  backend "s3" {
    bucket         = "plenora-terraform-state-112011134642"
    key            = "plenora-etl/lifecycle.tfstate"
    region         = "eu-south-1"
    dynamodb_table = "plenora-terraform-lock"
    encrypt        = true
  }
}

provider "aws" {
  region = var.aws_region

  default_tags {
    tags = {
      Project    = "PLENORA-ETL"
      ManagedBy  = "Terraform"
      Repository = "PlenoraETL/Plenora-backend"
    }
  }
}
