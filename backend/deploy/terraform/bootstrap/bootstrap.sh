#!/bin/bash
# ============================================================================
# PLENORA-ETL Terraform state backend bootstrap
# ============================================================================
# Crea le risorse AWS che fanno da backend per Terraform (state bucket S3 +
# lock DynamoDB). Una tantum. Idempotente — puoi rilanciarlo.
#
# Requisiti:
#   - aws CLI configurata (profilo con permessi s3:CreateBucket +
#     s3:PutBucketVersioning + s3:PutEncryptionConfiguration +
#     dynamodb:CreateTable nell'account target)
#   - bash
#
# Esegui da: backend/deploy/terraform/bootstrap/

set -euo pipefail

# ── Configurazione ──────────────────────────────────────────────────────────
AWS_ACCOUNT="112011134642"
AWS_REGION="eu-south-1"
STATE_BUCKET="plenora-terraform-state-${AWS_ACCOUNT}"
LOCK_TABLE="plenora-terraform-lock"

# ── Helpers ─────────────────────────────────────────────────────────────────
log() { echo "[bootstrap] $*"; }

check_aws_cli() {
    if ! command -v aws >/dev/null 2>&1; then
        log "ERRORE: aws CLI non trovato nel PATH."
        exit 1
    fi
    if ! aws sts get-caller-identity --output text >/dev/null 2>&1; then
        log "ERRORE: aws CLI non autenticato (aws sts get-caller-identity fallisce)."
        log "Configura con 'aws configure' o esporta AWS_PROFILE / AWS_ACCESS_KEY_ID+AWS_SECRET_ACCESS_KEY."
        exit 1
    fi
    local current_account
    current_account=$(aws sts get-caller-identity --query Account --output text)
    if [ "$current_account" != "$AWS_ACCOUNT" ]; then
        log "ERRORE: aws CLI autenticato sull'account $current_account, atteso $AWS_ACCOUNT."
        log "Esporta il profilo corretto prima di rieseguire."
        exit 1
    fi
    log "aws CLI OK (account=$current_account)"
}

# ── Step 1: S3 state bucket ─────────────────────────────────────────────────
create_state_bucket() {
    log "== S3 bucket state: $STATE_BUCKET =="

    if aws s3api head-bucket --bucket "$STATE_BUCKET" 2>/dev/null; then
        log "bucket gia' esistente, skip creazione"
    else
        log "creazione bucket..."
        # AWS S3 "eu-south-1" richiede LocationConstraint esplicito (non us-east-1)
        aws s3api create-bucket \
            --bucket "$STATE_BUCKET" \
            --region "$AWS_REGION" \
            --create-bucket-configuration "LocationConstraint=$AWS_REGION"
        log "bucket creato"
    fi

    log "abilito versioning..."
    aws s3api put-bucket-versioning \
        --bucket "$STATE_BUCKET" \
        --versioning-configuration Status=Enabled

    log "abilito encryption SSE-S3..."
    aws s3api put-bucket-encryption \
        --bucket "$STATE_BUCKET" \
        --server-side-encryption-configuration '{
            "Rules": [{
                "ApplyServerSideEncryptionByDefault": {"SSEAlgorithm": "AES256"},
                "BucketKeyEnabled": true
            }]
        }'

    log "blocco public access..."
    aws s3api put-public-access-block \
        --bucket "$STATE_BUCKET" \
        --public-access-block-configuration \
            "BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true"

    log "S3 bucket state configurato"
}

# ── Step 2: DynamoDB lock table ─────────────────────────────────────────────
create_lock_table() {
    log "== DynamoDB lock table: $LOCK_TABLE =="

    if aws dynamodb describe-table --table-name "$LOCK_TABLE" --region "$AWS_REGION" >/dev/null 2>&1; then
        log "table gia' esistente, skip creazione"
        return
    fi

    log "creazione table..."
    aws dynamodb create-table \
        --table-name "$LOCK_TABLE" \
        --attribute-definitions AttributeName=LockID,AttributeType=S \
        --key-schema AttributeName=LockID,KeyType=HASH \
        --billing-mode PAY_PER_REQUEST \
        --region "$AWS_REGION" >/dev/null

    log "attendo che la tabella sia ACTIVE..."
    aws dynamodb wait table-exists --table-name "$LOCK_TABLE" --region "$AWS_REGION"
    log "DynamoDB lock table pronta"
}

# ── Main ────────────────────────────────────────────────────────────────────
main() {
    check_aws_cli
    create_state_bucket
    create_lock_table

    log ""
    log "=========================================="
    log "Bootstrap completato."
    log "=========================================="
    log "Backend state:    s3://$STATE_BUCKET"
    log "Lock table:       $LOCK_TABLE (region $AWS_REGION)"
    log ""
    log "Prossimi passi:"
    log "  cd .."
    log "  terraform init"
    log "  terraform import aws_s3_bucket_lifecycle_configuration.backup \\"
    log "      plenora-backup-${AWS_ACCOUNT}-${AWS_REGION}-an"
    log "  terraform plan"
    log "  terraform apply"
}

main "$@"
