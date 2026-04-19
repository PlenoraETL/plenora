#!/bin/bash
# ============================================================================
# URBY-ETL → PLENORA-ETL infrastructure rename — server migration
# ============================================================================
# Esegue la migrazione lato server del rebrand prodotto:
#   - Directory: /home/ubuntu/urby-etl → /home/ubuntu/plenora-etl
#   - Systemd units: urby-backup{,-test}.{service,timer},
#                    urby-{backend,celery,beat}.service
#                    → plenora-* con path aggiornati
#   - Lock/log files in /tmp: urby-backup-manual.* → plenora-backup-manual.*
#
# Downtime atteso: ~60-90s (stop servizi, rename dir, reload, restart).
#
# Idempotenza: esegue sanity check all'inizio. Se la migrazione risulta
# gia' fatta (nuova dir e nuovi unit presenti), esce con exit 0 senza
# toccare nulla.
#
# Rollback automatico: se un qualunque step fallisce, ripristina lo stato
# pre-migration. Preserva backup dei vecchi unit files in /etc/systemd/
# system/*.bak per 7 giorni (remove manuale a conferma stabilita').
#
# Requisiti per l'esecuzione:
#   - root (via sudo)
#   - Il server sta girando con layout vecchio (/home/ubuntu/urby-etl)
#   - I 5 service `urby-*` sono enabled + active (o almeno installed)
#
# Esecuzione:
#   cd /home/ubuntu/urby-etl/backend
#   git pull origin main
#   sudo bash deploy/migrations/2026-04-18_rename-urby-to-plenora.sh
#
# Dopo l'esecuzione:
#   1. Verifica che i nuovi plenora-* services siano attivi
#   2. Verifica che /home/ubuntu/plenora-etl/backend/.env esista
#   3. Segnala a Claude per il commit di Fase 3 (deploy.sh + cleanup refs)
# ============================================================================

set -euo pipefail

# ── Configurazione ──────────────────────────────────────────────────────────

OLD_DIR="/home/ubuntu/urby-etl"
NEW_DIR="/home/ubuntu/plenora-etl"
SYSTEMD_DIR="/etc/systemd/system"

# Services "repo-managed": il contenuto nuovo e' nel repo in deploy/systemd/
REPO_UNITS=(
    "plenora-backup.service"
    "plenora-backup.timer"
    "plenora-backup-test.service"
    "plenora-backup-test.timer"
)

# Services "server-only": il contenuto non e' nel repo, va tradotto sed
# dal vecchio file installato sul server.
SERVER_ONLY_MAPPING=(
    "urby-backend.service:plenora-backend.service"
    "urby-celery.service:plenora-celery.service"
    "urby-beat.service:plenora-beat.service"
)

# Vecchi unit da stoppare/disable/rimuovere a fine migration
OLD_UNITS=(
    "urby-backup.timer"
    "urby-backup-test.timer"
    "urby-backup.service"
    "urby-backup-test.service"
    "urby-backend.service"
    "urby-celery.service"
    "urby-beat.service"
)

# Lock/log files host-local da rinominare se presenti
OLD_LOCK="/tmp/urby-backup-manual.lock"
OLD_LOG="/tmp/urby-backup-manual.log"
NEW_LOCK="/tmp/plenora-backup-manual.lock"
NEW_LOG="/tmp/plenora-backup-manual.log"

BACKUP_SUFFIX=".pre-plenora-rename.bak"

# ── Logging ─────────────────────────────────────────────────────────────────

log()   { echo "[migrate] $*"; }
error() { echo "[migrate][ERROR] $*" >&2; }

# ── Rollback ────────────────────────────────────────────────────────────────
# Stato tracciato: cosa abbiamo gia' cambiato, in ordine. Consente undo
# selettivo.

_state_dir_renamed=false
_state_new_units_installed=()

rollback() {
    error "ROLLBACK in corso..."

    # Stop nuovi services se partiti
    for unit in "${_state_new_units_installed[@]}"; do
        systemctl stop "$unit" 2>/dev/null || true
        systemctl disable "$unit" 2>/dev/null || true
        rm -f "$SYSTEMD_DIR/$unit"
    done
    if [ ${#_state_new_units_installed[@]} -gt 0 ]; then
        systemctl daemon-reload
    fi

    # Rinomina dir indietro
    if $_state_dir_renamed; then
        if [ -d "$NEW_DIR" ] && [ ! -d "$OLD_DIR" ]; then
            mv "$NEW_DIR" "$OLD_DIR"
        fi
    fi

    # Riprendi vecchi services
    for unit in "${OLD_UNITS[@]}"; do
        bak="$SYSTEMD_DIR/$unit$BACKUP_SUFFIX"
        target="$SYSTEMD_DIR/$unit"
        if [ -f "$bak" ] && [ ! -f "$target" ]; then
            cp "$bak" "$target"
        fi
    done
    systemctl daemon-reload

    # Re-enable + start dei 3 long-running (timer li lascio come erano)
    systemctl enable urby-backend.service urby-celery.service urby-beat.service 2>/dev/null || true
    systemctl start urby-backend.service urby-celery.service urby-beat.service 2>/dev/null || true
    systemctl enable urby-backup.timer urby-backup-test.timer 2>/dev/null || true
    systemctl start urby-backup.timer urby-backup-test.timer 2>/dev/null || true

    error "ROLLBACK completato. Verifica manualmente lo stato con:"
    error "  systemctl status urby-backend urby-celery urby-beat"
    exit 1
}

trap rollback ERR

# ── Sanity check ─────────────────────────────────────────────────────────────

log "=== PLENORA rename migration start ==="

if [ "$EUID" -ne 0 ]; then
    error "Va eseguito come root (via sudo)."
    exit 1
fi

# Idempotenza: se gia' migrato, exit
if [ -d "$NEW_DIR" ] && [ ! -d "$OLD_DIR" ]; then
    if systemctl list-unit-files --full --no-legend | grep -q '^plenora-backend.service'; then
        log "Migration gia' applicata ($NEW_DIR presente, plenora-backend installato). Exit 0."
        exit 0
    fi
fi

if [ ! -d "$OLD_DIR" ]; then
    error "$OLD_DIR non esiste. Stato server inatteso."
    exit 1
fi

# Verifica che i 5 vecchi service siano almeno installati
for unit in "${OLD_UNITS[@]}"; do
    if [ ! -f "$SYSTEMD_DIR/$unit" ]; then
        error "$SYSTEMD_DIR/$unit non trovato. Stato server inatteso."
        exit 1
    fi
done

# Verifica che il repo pullato sia gia' quello nuovo (contiene i file plenora-*)
for unit in "${REPO_UNITS[@]}"; do
    src="$OLD_DIR/backend/deploy/systemd/$unit"
    if [ ! -f "$src" ]; then
        error "Repo non contiene $src. Fai 'git pull origin main' prima di rieseguire."
        exit 1
    fi
done

log "Sanity check OK"

# ── Step 1: backup vecchi unit file ─────────────────────────────────────────

log "== Step 1/8: backup vecchi unit file =="
for unit in "${OLD_UNITS[@]}"; do
    cp -a "$SYSTEMD_DIR/$unit" "$SYSTEMD_DIR/$unit$BACKUP_SUFFIX"
    log "  backup: $unit -> ${unit}${BACKUP_SUFFIX}"
done

# ── Step 2: stop + disable vecchi services ───────────────────────────────────

log "== Step 2/8: stop + disable vecchi services/timers =="
# Timer prima per evitare trigger accidentale durante la migrazione
systemctl stop urby-backup.timer urby-backup-test.timer 2>/dev/null || true
systemctl stop urby-backup.service urby-backup-test.service 2>/dev/null || true
systemctl stop urby-backend.service urby-celery.service urby-beat.service
systemctl disable urby-backup.timer urby-backup-test.timer 2>/dev/null || true
systemctl disable urby-backend.service urby-celery.service urby-beat.service 2>/dev/null || true
log "  vecchi services fermati + disabled (downtime iniziato)"

# ── Step 3: rename dir ──────────────────────────────────────────────────────

log "== Step 3/8: rename dir =="
mv "$OLD_DIR" "$NEW_DIR"
_state_dir_renamed=true
log "  $OLD_DIR -> $NEW_DIR"

# ── Step 3.5: fix shebang dentro venv ───────────────────────────────────────
# I file in venv/bin/* (celery, uvicorn, pip, ecc.) hanno shebang Python
# hardcoded che punta all'interprete del vecchio path. Dopo il rename dir,
# gli shebang ancora dicono /home/ubuntu/urby-etl/... che non esiste piu'
# → exec fallisce. Sostituiamo il path in TUTTI gli script del venv.
# Idem pyvenv.cfg che ha un `home = ...` assoluto.

log "== Step 3.5/8: fix venv shebangs + pyvenv.cfg =="
VENV="$NEW_DIR/backend/venv"
if [ ! -d "$VENV" ]; then
    error "venv non trovato in $VENV (atteso dopo rename dir)"
    exit 1
fi

if [ -f "$VENV/pyvenv.cfg" ]; then
    sed -i "s|/home/ubuntu/urby-etl|/home/ubuntu/plenora-etl|g" "$VENV/pyvenv.cfg"
    log "  aggiornato $VENV/pyvenv.cfg"
fi

# Fix shebangs in tutti gli script del venv/bin.
# Uso grep -l per listare solo file che contengono il path vecchio
# (alcuni binari compilati NON sono script e non vanno toccati).
fixed_count=0
while IFS= read -r f; do
    sed -i "s|/home/ubuntu/urby-etl|/home/ubuntu/plenora-etl|g" "$f"
    fixed_count=$((fixed_count + 1))
done < <(grep -rl "/home/ubuntu/urby-etl" "$VENV/bin/" 2>/dev/null || true)
log "  aggiornati $fixed_count script in $VENV/bin/"

# ── Step 4: genera nuovi unit per server-only (backend/celery/beat) ─────────

log "== Step 4/8: genera plenora-{backend,celery,beat}.service via sed =="
for mapping in "${SERVER_ONLY_MAPPING[@]}"; do
    old_unit="${mapping%%:*}"
    new_unit="${mapping##*:}"
    old_path="$SYSTEMD_DIR/$old_unit"
    new_path="$SYSTEMD_DIR/$new_unit"

    # Sostituzioni:
    #   /home/ubuntu/urby-etl -> /home/ubuntu/plenora-etl  (path della code)
    #   urby-backend / urby-celery / urby-beat -> plenora-*   (riferimenti cross-unit)
    sed \
        -e "s|/home/ubuntu/urby-etl|/home/ubuntu/plenora-etl|g" \
        -e "s|urby-backend|plenora-backend|g" \
        -e "s|urby-celery|plenora-celery|g" \
        -e "s|urby-beat|plenora-beat|g" \
        "$old_path" > "$new_path"
    chmod 644 "$new_path"
    _state_new_units_installed+=("$new_unit")
    log "  generato: $new_path"
done

# ── Step 5: copia unit repo-managed ─────────────────────────────────────────

log "== Step 5/8: copia unit repo-managed in /etc/systemd/system =="
for unit in "${REPO_UNITS[@]}"; do
    src="$NEW_DIR/backend/deploy/systemd/$unit"
    dst="$SYSTEMD_DIR/$unit"
    cp "$src" "$dst"
    chmod 644 "$dst"
    _state_new_units_installed+=("$unit")
    log "  installato: $dst"
done

# ── Step 6: daemon-reload + enable + start nuovi ────────────────────────────

log "== Step 6/8: daemon-reload + enable + start nuovi services =="
systemctl daemon-reload

# Enable
systemctl enable plenora-backend.service plenora-celery.service plenora-beat.service
systemctl enable plenora-backup.timer plenora-backup-test.timer

# Start long-running
systemctl start plenora-backend.service plenora-celery.service plenora-beat.service
# Start timer (service oneshot parte al prossimo OnCalendar)
systemctl start plenora-backup.timer plenora-backup-test.timer
log "  services avviati (downtime finito)"

# ── Step 7: verifica che i nuovi siano active ───────────────────────────────

log "== Step 7/8: verifica active state =="
sleep 5  # margine per startup
for unit in plenora-backend.service plenora-celery.service plenora-beat.service; do
    state=$(systemctl is-active "$unit")
    if [ "$state" != "active" ]; then
        error "$unit non e' active (stato=$state). Avvio rollback."
        exit 1
    fi
    log "  $unit: $state"
done

for timer in plenora-backup.timer plenora-backup-test.timer; do
    state=$(systemctl is-active "$timer")
    if [ "$state" != "active" ]; then
        error "$timer non e' active (stato=$state). Avvio rollback."
        exit 1
    fi
    log "  $timer: $state"
done

# ── Step 8: rinomina lock/log files /tmp se presenti ────────────────────────

log "== Step 8/8: rename lock/log files /tmp =="
if [ -f "$OLD_LOCK" ]; then
    mv "$OLD_LOCK" "$NEW_LOCK"
    log "  $OLD_LOCK -> $NEW_LOCK"
fi
if [ -f "$OLD_LOG" ]; then
    mv "$OLD_LOG" "$NEW_LOG"
    log "  $OLD_LOG -> $NEW_LOG"
fi

# Disarma il trap (tutto ok da qui)
trap - ERR

# ── Completato ──────────────────────────────────────────────────────────────

log ""
log "=========================================="
log "MIGRATION COMPLETATA CON SUCCESSO"
log "=========================================="
log "Services attivi: plenora-backend, plenora-celery, plenora-beat"
log "Timer attivi: plenora-backup.timer, plenora-backup-test.timer"
log "Nuovo path: $NEW_DIR"
log ""
log "Backup vecchi unit file: $SYSTEMD_DIR/*$BACKUP_SUFFIX"
log "  (safe da rimuovere dopo 7gg di stabilita')"
log ""
log "Prossimi passi:"
log "  1. Verifica: curl -sI localhost:<porta-backend>/health"
log "  2. Segnala a Claude per il commit di Fase 3"
log "     (deploy.sh aggiornato + cleanup path references nel codice)"
log ""
log "Rollback manuale (se serve, entro 7gg):"
log "  - Stop nuovi: sudo systemctl stop plenora-{backup.timer,backup-test.timer,backend,celery,beat}"
log "  - rm /etc/systemd/system/plenora-*"
log "  - Ripristina bak: cd $SYSTEMD_DIR && for f in *$BACKUP_SUFFIX; do mv \"\$f\" \"\${f%$BACKUP_SUFFIX}\"; done"
log "  - mv $NEW_DIR $OLD_DIR"
log "  - systemctl daemon-reload && systemctl enable --now urby-{backend,celery,beat}"
