# SFTP test keys — fake, test-only

Le chiavi SSH presenti in questa directory sono **fake**, generate
esclusivamente per l'ambiente E2E plenora (`test-e2e-v2`) e volutamente
committate nel repository.

## File

| File                          | Tipo        | Passphrase                  | Mode |
|-------------------------------|-------------|-----------------------------|------|
| `id_rsa_test`                 | private RSA 2048 | _(nessuna)_            | 600  |
| `id_rsa_test.pub`             | public  RSA 2048 | —                       | 644  |
| `id_rsa_test_passphrase`      | private RSA 2048 | `plenora_passphrase_test` | 600  |
| `id_rsa_test_passphrase.pub`  | public  RSA 2048 | —                       | 644  |

## Garanzie

- **Generate da zero** per questo repo (`ssh-keygen -t rsa -b 2048 ...`).
- **Mai usate** in altri repo, sistemi, host o account.
- **Nessun valore reale** dietro: passphrase, commenti, fingerprint
  esistono solo qui.
- **NON** vanno usate in ambienti diversi dal compose E2E plenora.
- **NON** vanno copiate in `~/.ssh/` né distribuite a utenti.

## Uso

Il container `sftp` (atmoz/sftp) carica automaticamente le `*.pub` da
`/home/<user>/.ssh/keys/`. Le chiavi private servono al lato client
(test Python) per autenticarsi al container.

Variabili `.env`:

```
SFTP_PRIVATE_KEY_PATH=backend/test-e2e-v2/sftp/keys/id_rsa_test
SFTP_PRIVATE_KEY_PASSPHRASE_PATH=backend/test-e2e-v2/sftp/keys/id_rsa_test_passphrase
SFTP_PRIVATE_KEY_PASSPHRASE=plenora_passphrase_test
```

## Rigenerazione

Se servisse rigenerarle:

```fish
cd backend/test-e2e-v2/sftp/keys
rm -f id_rsa_test id_rsa_test.pub id_rsa_test_passphrase id_rsa_test_passphrase.pub
ssh-keygen -t rsa -b 2048 -f id_rsa_test -N "" -C "plenora-e2e-test"
ssh-keygen -t rsa -b 2048 -f id_rsa_test_passphrase -N "plenora_passphrase_test" -C "plenora-e2e-test-passphrase"
chmod 600 id_rsa_test id_rsa_test_passphrase
chmod 644 id_rsa_test.pub id_rsa_test_passphrase.pub
```
