#!/usr/bin/env fish
# Plenora E2E — verifica veloce dello stato dello stack.
#
# Mostra:
#   - stato dei container del root compose
#   - stato dei container del compose e2e
#   - health di fake-rest
#   - presenza network plenora-e2e
#   - presenza utente test in plenora
#
# Niente write, niente up/down. Solo lettura.

set -l SCRIPT_DIR (status dirname)
set -l REPO_ROOT (cd $SCRIPT_DIR/../../.. && pwd)
set -l E2E_DIR $REPO_ROOT/backend/test-e2e
set -l ROOT_COMPOSE $REPO_ROOT/docker-compose.yml
set -l E2E_COMPOSE $E2E_DIR/docker/docker-compose.yml

echo "═══ Stack app plenora ═══"
docker compose -f $ROOT_COMPOSE ps 2>/dev/null
or echo "  (compose non avviato)"

echo ""
echo "═══ Stack fixture e2e ═══"
docker compose -f $E2E_COMPOSE --profile full ps 2>/dev/null
or echo "  (compose non avviato)"

echo ""
echo "═══ Network plenora-e2e ═══"
docker network inspect plenora-e2e --format '{{.Name}} (driver={{.Driver}}, scope={{.Scope}})' 2>/dev/null
or echo "  Network NON presente. Crea con: docker network create plenora-e2e"

echo ""
echo "═══ Health fake-rest ═══"
curl -fsS http://localhost:8080/health 2>/dev/null
and echo
or echo "  fake-rest non raggiungibile su localhost:8080"

echo ""
echo "═══ Health backend plenora ═══"
curl -fsS http://localhost:5004/health 2>/dev/null
and echo
or curl -fsS http://localhost:5004/api/health 2>/dev/null
and echo
or echo "  backend non raggiungibile su localhost:5004"

echo ""
echo "═══ Utente test in plenora DB ═══"
docker exec plenora-postgres-1 psql -U plenora -d plenora -tAc \
    "SELECT email FROM users WHERE email='tester@plenora.test'" 2>/dev/null
or echo "  utente test non trovato (o postgres app non up)"
