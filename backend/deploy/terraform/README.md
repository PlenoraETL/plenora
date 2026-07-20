# PLENORA-ETL Terraform — AWS infra IaC

Gestione infrastruttura AWS riproducibile via Terraform. Sostituisce le
configurazioni manuali in Console con codice versionato.

## Cosa c'e' qui oggi

| Risorsa | Definita in | Scope |
|---|---|---|
| Lifecycle backup DB | `lifecycle.tf` | `plenora_*` (30gg→IA→365gg delete) |
| Lifecycle artifact runtime | `lifecycle.tf` | tag `plenora-retention=ephemeral` (7gg delete), legacy prefix e multipart incompleti |

## Cosa NON c'e' (ancora)

- **Bucket S3** stesso (rimane gestito manualmente, evita chicken-and-egg col Terraform state)
- **IAM role** `plenora-backup-role` (manuale, vedi `deploy/iam/`)
- **KMS keys**, **SSM parameters**, **EC2**, **RDS**, **VPC** — tutto fuori scope per questa prima iterazione

Scope pieno IaC = lavoro successivo dopo che la baseline lifecycle e' stabile.

## Prerequisiti

- Terraform >= 1.5
- AWS CLI configurata con credenziali che hanno:
  - `s3:GetLifecycleConfiguration`, `s3:PutLifecycleConfiguration` sui bucket backup e runtime
  - `s3:*` sul bucket state (creato dal bootstrap)
  - `dynamodb:*` sulla tabella lock
- Git: questo repo clonato

## Prima volta (bootstrap)

Lo state Terraform vive su S3 + DynamoDB. Queste risorse vanno create una
sola volta con lo script in `bootstrap/`. Istruzioni complete in
[bootstrap/README.md](bootstrap/README.md).

```bash
cd deploy/terraform/bootstrap
bash bootstrap.sh
```

## Import del bucket esistente

La lifecycle rule `urby_*` esiste gia' sul bucket (configurata in Console).
Prima del primo `terraform apply`, importala nel state cosi' Terraform
non la ricrea da zero:

```bash
cd deploy/terraform
terraform init  # dopo il bootstrap
terraform import aws_s3_bucket_lifecycle_configuration.backup \
    plenora-backup-112011134642-eu-south-1-an
# Solo se il bucket runtime ha già una lifecycle gestita manualmente:
terraform import aws_s3_bucket_lifecycle_configuration.runtime \
    plenora-runtime-112011134642-eu-south-1-an
```

Dopo l'import, `terraform plan` mostra separatamente la retention backup e
quella del bucket runtime. La seconda include tag effimeri, compatibilità con
i prefissi legacy e cleanup dei multipart incompleti.

## Flusso standard

```bash
cd deploy/terraform
terraform init
terraform plan    # sempre prima di apply, review dei diff
terraform apply   # conferma esplicita richiesta
```

## Rollback

Lifecycle rule non sono distruttive — gli oggetti esistenti non vengono
toccati istantaneamente, la rule e' un trigger che agisce al prossimo
passaggio del lifecycle engine S3 (~24h latency tipica).

Se un apply rompe qualcosa:
1. `terraform plan -destroy -target=aws_s3_bucket_lifecycle_configuration.backup`
2. Verifica il diff
3. Se vuoi solo ripristinare la vecchia rule: modifica il codice, re-apply
4. Ultima risorsa: Console AWS, "Edit lifecycle" e ripristina manualmente

## File

```
terraform/
  README.md               # questo file
  main.tf                 # provider + backend config
  variables.tf            # input vars
  lifecycle.tf            # lifecycle rules per i 3 prefissi
  outputs.tf              # output per conferma post-apply
  terraform.tfvars.example # template per i valori reali (non committare quelli veri)
  .gitignore              # ignora .terraform/, *.tfstate*, terraform.tfvars
  bootstrap/
    README.md             # istruzioni una-tantum
    bootstrap.sh          # script creazione bucket state + DDB lock
```
