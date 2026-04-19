# Terraform state bootstrap

Una-tantum: crea le risorse AWS che fanno da **backend** per Terraform
(bucket S3 per lo state + tabella DynamoDB per i lock su apply).

## Perche' bootstrap separato

Terraform ha un problema chicken-and-egg con il suo stesso backend:
per gestire il bucket state via Terraform servirebbe gia' avere un bucket
state. Quindi creiamo le risorse di supporto con AWS CLI una volta sola,
e poi Terraform gira sul backend pronto.

Queste risorse sono stabili (non cambiano mai dopo il setup), quindi non
c'e' valore a versionarle con Terraform. Se proprio si volesse IaC,
si potrebbe usare CloudFormation o un secondo progetto Terraform con
local state. Ma per singolo setup-and-forget, lo script e' ok.

## Requisiti

- `aws` CLI configurato con credenziali che hanno permessi
  `s3:CreateBucket`, `s3:PutBucketVersioning`, `s3:PutEncryptionConfiguration`,
  `dynamodb:CreateTable` nell'account target.
- bash (testato su WSL/Git Bash su Windows).

## Uso

```bash
cd backend/deploy/terraform/bootstrap
bash bootstrap.sh
```

Lo script e' idempotente: se le risorse esistono gia', stampa "already
exists" e continua. Puoi rilanciarlo senza rischi.

## Cosa viene creato

| Risorsa | Nome | Scopo |
|---|---|---|
| S3 bucket | `plenora-terraform-state-112011134642` | Storage per i `.tfstate` |
| S3 bucket versioning | Enabled | Rollback in caso di state corruption |
| S3 bucket encryption | AES256 | SSE-S3 default |
| S3 bucket public access | Block all | Safety |
| DynamoDB table | `plenora-terraform-lock` | Lock per prevenire apply concorrenti |

## Dopo il bootstrap

Torna nella dir parent e inizializza:

```bash
cd ..
terraform init
terraform import aws_s3_bucket_lifecycle_configuration.backup \
    plenora-backup-112011134642-eu-south-1-an
terraform plan
terraform apply
```

## Distruggere lo state backend

**Non farlo** a meno di essere sicuri. Se lo fai, tutto il tracking
Terraform va perso (pur restando le risorse AWS vive — dovranno essere
re-importate).

```bash
aws s3 rm s3://plenora-terraform-state-112011134642 --recursive
aws s3api delete-bucket --bucket plenora-terraform-state-112011134642
aws dynamodb delete-table --table-name plenora-terraform-lock
```
