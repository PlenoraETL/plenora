## Integration Tests

Layer dedicato ai test che integrano:

- backend reale
- PostgreSQL reale
- RabbitMQ reale (broker Dramatiq)
- side effect persistiti su DB

Questa cartella non usa `efm-backup`.

Perimetro consentito:

- `postgres` su `localhost:5432`
- `rabbitmq` su `localhost:5672`
- altri container solo quando servono davvero a un caso di test specifico

Pattern:

1. seed mirato con ID prefissati `it_*`
2. esecuzione via API o service reale
3. assert su DB reale
4. cleanup mirato per `group_id`/prefisso

Obiettivo:

- verificare che il backend non solo risponda, ma persista davvero i dati attesi
- coprire i contratti tra router/service/DB senza arrivare ancora a full functional orchestration
