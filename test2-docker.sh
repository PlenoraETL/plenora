#!/usr/bin/env sh
# Esegue la suite tests2 nel container plenora-test2.
#
#   ./test2-docker.sh                          # intera lane: pytest tests2 -q
#   ./test2-docker.sh tests2/shared -q         # selezione mirata
#   ./test2-docker.sh --build ...              # forza rebuild immagini prima
#
# Prima esecuzione: build dell'immagine backend (stack conda-forge, lenta)
# e poi del layer test. Le run successive riusano le immagini; il codice e'
# bind-mounted, quindi nessuna rebuild per modifiche ai sorgenti.

set -eu

cd "$(dirname "$0")"

HOST_UID="$(id -u)"
HOST_GID="$(id -g)"
export HOST_UID HOST_GID

if [ "${1:-}" = "--build" ]; then
    shift
    docker compose build backend
    docker compose -f docker-compose.test2.yml build test2
fi

if ! docker image inspect plenora-test2:latest >/dev/null 2>&1; then
    echo "[test2-docker] immagine assente: build iniziale (puo' richiedere minuti)..."
    docker compose build backend
    docker compose -f docker-compose.test2.yml build test2
fi

exec docker compose -f docker-compose.test2.yml run --rm test2 "$@"
