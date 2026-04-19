# IAM policies — plenora-backup-role

Policy JSON committate per disaster recovery e per documentare le permission
assegnate al ruolo EC2 dell'istanza di produzione (`plenora-backup-role`,
account `112011134642`).

## File

- `plenora-backup-ssm-read.json` — permette al ruolo di leggere i secret
  da AWS SSM Parameter Store su path `/plenora/prod/*` e decifrarli
  (condizione `kms:ViaService: ssm.eu-south-1`). Consumato da
  `scripts/load_secrets_from_ssm.py` via `ExecStartPre=` nei systemd
  unit di backup e test-restore.

## Applicare una policy (una tantum, ruolo EC2 gia' esistente)

Serve un profilo AWS con permessi `iam:PutRolePolicy` — tipicamente
l'admin account, non l'istanza EC2 (che non puo' modificare il proprio
role per design).

Da una shell con AWS CLI admin configurata (o da AWS CloudShell):

```bash
aws iam put-role-policy \
  --role-name plenora-backup-role \
  --policy-name plenora-backup-ssm-read \
  --policy-document file://plenora-backup-ssm-read.json
```

Verifica:

```bash
aws iam list-role-policies --role-name plenora-backup-role
aws iam get-role-policy \
  --role-name plenora-backup-role \
  --policy-name plenora-backup-ssm-read
```

## Popolare SSM (prima volta)

Dopo aver applicato la policy, popola i secret su Parameter Store. Valori
da leggere dal `backend/.env` legacy (da rimuovere dopo la migrazione).

```bash
# DATABASE_URL — SecureString con default KMS (alias/aws/ssm)
aws ssm put-parameter \
  --name /plenora/prod/DATABASE_URL \
  --value 'postgresql://user:pass@host:5432/db' \
  --type SecureString \
  --region eu-south-1 \
  --overwrite

# Verifica lettura (dovrebbe ritornare il valore decifrato)
aws ssm get-parameter \
  --name /plenora/prod/DATABASE_URL \
  --with-decryption \
  --region eu-south-1
```

## Post-migrazione

Una volta che i servizi leggono da SSM con successo, `backend/.env`
puo' essere rimosso dal server (tenere una copia offline di emergenza).
I servizi `plenora-backup.service` e `plenora-backup-test.service` non
dipendono piu' dal file.
