#!/bin/bash
# =============================================================================
# run_coverage.sh — Coverage file-per-file per evitare saturazione RAM
#
# Uso:
#   ./run_coverage.sh                    # tutti i moduli unit
#   ./run_coverage.sh estrai             # solo modulo estrai
#   ./run_coverage.sh manipola carica    # più moduli
#
# Prerequisiti: backend (8000), celery, mock_api (8999) devono essere attivi.
# =============================================================================

cd "$(dirname "$0")"

BACKEND_DIR="../backend"
ALL_MODULES=(estrai manipola arricchisci carica scheduler security error_handling logs performance regression)

# Moduli da testare (argomenti o tutti)
if [ $# -gt 0 ]; then
  MODULES=("$@")
else
  MODULES=("${ALL_MODULES[@]}")
fi

# Verifica servizi
echo "=== Verifica servizi ==="
BACKEND_OK=$(curl -s -o /dev/null -w "%{http_code}" http://localhost:8000/docs 2>/dev/null || echo "000")
MOCK_OK=$(curl -s -o /dev/null -w "%{http_code}" http://localhost:8999/docs 2>/dev/null || echo "000")

if [ "$BACKEND_OK" != "200" ]; then
  echo "ERRORE: Backend non raggiungibile (porta 8000). Avvialo prima."
  exit 1
fi
if [ "$MOCK_OK" != "200" ]; then
  echo "ERRORE: Mock API non raggiungibile (porta 8999). Avvialo prima."
  exit 1
fi
echo "Backend: OK | Mock API: OK"

# Pulisci coverage precedente
rm -f .coverage .coverage.* 2>/dev/null

TOTAL_FILES=0
TOTAL_PASSED=0
TOTAL_FAILED=0
TOTAL_SKIPPED=0

for MODULE in "${MODULES[@]}"; do
  UNIT_DIR="${MODULE}/unit"
  if [ ! -d "$UNIT_DIR" ]; then
    echo "WARN: ${UNIT_DIR} non esiste, skip."
    continue
  fi

  FILES=$(ls ${UNIT_DIR}/test_*.py 2>/dev/null || true)
  if [ -z "$FILES" ]; then
    echo "WARN: nessun test in ${UNIT_DIR}, skip."
    continue
  fi

  FILE_COUNT=$(echo "$FILES" | wc -l)
  echo ""
  echo "================================================================="
  echo "  MODULO: ${MODULE} (${FILE_COUNT} file)"
  echo "================================================================="

  COUNT=0
  for f in $FILES; do
    COUNT=$((COUNT + 1))
    TOTAL_FILES=$((TOTAL_FILES + 1))
    echo "----- [${COUNT}/${FILE_COUNT}] ${f} -----"
    OUTPUT=$(pytest --cov=app --cov-append --cov-report= "$f" -n auto --tb=short -q 2>&1)
    LAST_LINE=$(echo "$OUTPUT" | grep -E "passed|failed|error|skipped" | tail -1)
    echo "  $LAST_LINE"

    # Conta risultati (senza grep -P per compatibilita Windows)
    P=$(echo "$LAST_LINE" | sed -n 's/.*\([0-9]*\) passed.*/\1/p')
    F=$(echo "$LAST_LINE" | sed -n 's/.*\([0-9]*\) failed.*/\1/p')
    S=$(echo "$LAST_LINE" | sed -n 's/.*\([0-9]*\) skipped.*/\1/p')
    TOTAL_PASSED=$((TOTAL_PASSED + ${P:-0}))
    TOTAL_FAILED=$((TOTAL_FAILED + ${F:-0}))
    TOTAL_SKIPPED=$((TOTAL_SKIPPED + ${S:-0}))
  done
done

echo ""
echo "================================================================="
echo "  RIEPILOGO"
echo "================================================================="
echo "File:    ${TOTAL_FILES}"
echo "Passed:  ${TOTAL_PASSED}"
echo "Failed:  ${TOTAL_FAILED}"
echo "Skipped: ${TOTAL_SKIPPED}"
echo ""

echo "=== Generazione report ==="
coverage report --show-missing
coverage html -d htmlcov
echo ""
echo "Report HTML: htmlcov/index.html"
echo "FATTO!"
