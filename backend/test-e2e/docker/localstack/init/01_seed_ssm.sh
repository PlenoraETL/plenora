#!/bin/sh
# Seed SSM Parameter Store + S3 bucket per i test E2E plenora.
set -eu

awslocal ssm put-parameter \
  --name "/plenora/test/db_password" \
  --value "plenora_test_pwd" \
  --type SecureString \
  --overwrite

awslocal ssm put-parameter \
  --name "/plenora/test/api_key" \
  --value "PLENORA_TEST_KEY" \
  --type SecureString \
  --overwrite

awslocal ssm put-parameter \
  --name "/plenora/test/oauth_secret" \
  --value "plenora_secret" \
  --type SecureString \
  --overwrite

# Bucket dedicato per i backup admin
awslocal s3api create-bucket --bucket plenora-backups || true
awslocal s3api create-bucket --bucket plenora-log-archive || true

echo "[plenora-e2e] LocalStack SSM + S3 buckets seeded."
