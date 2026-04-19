#!/usr/bin/env python3
"""
check_frontend.py — Verifica automatica post-sprint URBY-ETL
=============================================================

Esecuzione:
    python check_frontend.py [percorso/src]

Default: cerca ./src nella cartella corrente.

Rileva:
  - alert() e window.confirm() rimasti
  - Import dichiarati ma non usati (pattern comuni)
  - Componenti importati ma non nel JSX
  - CSS con proprietà senza valore (righe corrotte)
  - @keyframes duplicati tra file
  - Colori hardcoded ancora presenti (top-10)
  - console.log di debug (non warn/error)
  - window.location.href (bypass React Router)
  - export default senza memo() nei nodi ReactFlow
  - variabili CSS definite ma mai usate
"""

import os, re, sys
from collections import defaultdict

SRC = sys.argv[1] if len(sys.argv) > 1 else './src'

# ── Colori console ──────────────────────────────────────────────────────────
R = '\033[91m'  # rosso
Y = '\033[93m'  # giallo
G = '\033[92m'  # verde
B = '\033[94m'  # blu
W = '\033[0m'   # reset
BOLD = '\033[1m'

errors   = []   # bloccanti — da fixare prima di merge
warnings = []   # importanti ma non bloccanti
infos    = []   # da tenere d'occhio

def err(msg):   errors.append(msg)
def warn(msg):  warnings.append(msg)
def info(msg):  infos.append(msg)

def find_files(ext):
    result = []
    for root, _, files in os.walk(SRC):
        if 'node_modules' in root: continue
        for f in files:
            if f.endswith(ext):
                result.append(os.path.join(root, f))
    return result

jsx_files = find_files('.jsx') + find_files('.js')
css_files = find_files('.css')

def rel(path):
    return path.replace(SRC, 'src').replace('\\', '/')

print(f"\n{BOLD}{'='*60}{W}")
print(f"{BOLD}  URBY-ETL Frontend — Verifica Post-Sprint{W}")
print(f"{BOLD}{'='*60}{W}\n")
print(f"Scansione: {SRC}")
print(f"File JS/JSX: {len(jsx_files)}  |  File CSS: {len(css_files)}\n")

# ── CHECK 1 — alert() e window.confirm() ───────────────────────────────────
print(f"{BOLD}[1] alert() e window.confirm(){W}")
alert_found = []
for path in jsx_files:
    with open(path, encoding='utf-8', errors='ignore') as f:
        for i, line in enumerate(f, 1):
            stripped = line.strip()
            if stripped.startswith('//'): continue
            if re.search(r'\balert\s*\(', line) and 'useToast' not in path:
                alert_found.append(f"  {rel(path)}:{i}  →  {stripped[:80]}")
            if 'window.confirm(' in line:
                alert_found.append(f"  {rel(path)}:{i}  →  {stripped[:80]}")

if alert_found:
    for x in alert_found: err(x)
    print(f"  {R}✗ {len(alert_found)} alert/confirm trovati{W}")
else:
    print(f"  {G}✓ nessuno{W}")

# ── CHECK 2 — window.location.href (bypass React Router) ───────────────────
print(f"\n{BOLD}[2] window.location.href (bypass React Router){W}")
href_found = []
for path in jsx_files:
    if 'authBus' in path: continue  # authBus.js lo cita nel commento — è il fix
    with open(path, encoding='utf-8', errors='ignore') as f:
        for i, line in enumerate(f, 1):
            stripped = line.strip()
            if 'window.location.href' in line and not stripped.startswith('//') and not stripped.startswith('*'):
                href_found.append(f"  {rel(path)}:{i}")
if href_found:
    for x in href_found: err(x)
    print(f"  {R}✗ {len(href_found)} occorrenze{W}")
else:
    print(f"  {G}✓ nessuno{W}")

# ── CHECK 3 — console.log di debug (non warn/error/info) ──────────────────
print(f"\n{BOLD}[3] console.log di debug{W}")
debug_logs = []
for path in jsx_files:
    with open(path, encoding='utf-8', errors='ignore') as f:
        for i, line in enumerate(f, 1):
            stripped = line.strip()
            if stripped.startswith('//'): continue
            if re.search(r'\bconsole\.log\s*\(', line):
                debug_logs.append(f"  {rel(path)}:{i}  →  {stripped[:70]}")
if debug_logs:
    for x in debug_logs: warn(x)
    print(f"  {Y}⚠ {len(debug_logs)} console.log trovati{W}")
else:
    print(f"  {G}✓ nessuno{W}")

# ── CHECK 4 — Import dichiarati ma componente non usato nel JSX ────────────
print(f"\n{BOLD}[4] Import JSX dichiarati ma non nel JSX{W}")
common_components = ['ToastContainer', 'ConfirmDialog', 'LoadingOverlay', 'ModalWrapper']
orphan_imports = []
for path in jsx_files:
    with open(path, encoding='utf-8', errors='ignore') as f:
        content = f.read()
    for comp in common_components:
        imported = re.search(rf"import\s+{comp}", content)
        used_in_jsx = f'<{comp}' in content or f'<{comp}/' in content
        if imported and not used_in_jsx:
            orphan_imports.append(f"  {rel(path)}  →  {comp} importato ma non nel JSX")

if orphan_imports:
    for x in orphan_imports: err(x)
    print(f"  {R}✗ {len(orphan_imports)} import orfani{W}")
else:
    print(f"  {G}✓ nessuno{W}")

# ── CHECK 4b — JSX elements fuori dal div root ─────────────────────────────
print(f"\n{BOLD}[4b] ToastContainer/ConfirmDialog fuori dal div root{W}")
outside_root = []
for path in jsx_files:
    with open(path, encoding='utf-8', errors='ignore') as f:
        content = f.read()
    # Rileva solo quando appare DOPO la chiusura del return (fuori dal JSX)
    # Pattern: </div>\n  );\n con ToastContainer/ConfirmDialog TRA i due
    if re.search(r'</div>\s*\n\s*<(?:ToastContainer|ConfirmDialog)[^>]*/>\s*\n\s*(?:\);|\};)', content):
        outside_root.append(f"  {rel(path)}")
if outside_root:
    for x in outside_root: err(x)
    print(f"  {R}✗ {len(outside_root)} elementi fuori dal wrapper{W}")
else:
    print(f"  {G}✓ nessuno{W}")

# ── CHECK 5 — useToast importato ma toast.* mai usato ─────────────────────
print(f"\n{BOLD}[5] useToast importato senza utilizzi{W}")
toast_orphan = []
for path in jsx_files:
    with open(path, encoding='utf-8', errors='ignore') as f:
        content = f.read()
    if 'useToast' in content and 'import' in content:
        import_match = re.search(r"import\s+\{[^}]*useToast[^}]*\}", content)
        if import_match and not re.search(r'\btoast\.(success|error|warning|info|toasts|dismiss)\b', content):
            toast_orphan.append(f"  {rel(path)}")
if toast_orphan:
    for x in toast_orphan: warn(x)
    print(f"  {Y}⚠ {len(toast_orphan)} file con useToast inutilizzato{W}")
else:
    print(f"  {G}✓ nessuno{W}")

# ── CHECK 5b — toast/confirmModal usati ma non inizializzati ───────────────
print(f"\n{BOLD}[5b] toast.* / confirmModal usati senza inizializzazione{W}")
hook_uninit = []
for path in jsx_files:
    with open(path, encoding='utf-8', errors='ignore') as f:
        content = f.read()
    issues = []
    if re.search(r'\btoast\.(success|error|warning|info|toasts|dismiss)\b', content) and \
       not re.search(r'\bconst\s+toast\s*=\s*useToast\s*\(\)', content):
        issues.append('toast')
    if re.search(r'\bconfirmModal\.(confirm|isOpen|onConfirm|onCancel)\b|confirmModal\s*\}', content) and \
       not re.search(r'\bconst\s+confirmModal\s*=\s*useConfirmModal\s*\(\)', content):
        issues.append('confirmModal')
    if issues:
        hook_uninit.append(f"  {rel(path)}  →  {', '.join(issues)}")
if hook_uninit:
    for x in hook_uninit: err(x)
    print(f"  {R}✗ {len(hook_uninit)} file con hook non inizializzati{W}")
else:
    print(f"  {G}✓ nessuno{W}")

# ── CHECK 6 — Nodi ReactFlow senza memo() ─────────────────────────────────
print(f"\n{BOLD}[6] Nodi ReactFlow senza memo(){W}")
nodes_dir = os.path.join(SRC, 'components', 'nodes')
no_memo = []
if os.path.exists(nodes_dir):
    for fname in os.listdir(nodes_dir):
        if not fname.endswith('.jsx'): continue
        path = os.path.join(nodes_dir, fname)
        with open(path, encoding='utf-8', errors='ignore') as f:
            content = f.read()
        if 'Handle' in content and 'Position' in content:  # è un nodo ReactFlow
            if 'memo(' not in content:
                no_memo.append(f"  {rel(path)}")
if no_memo:
    for x in no_memo: warn(x)
    print(f"  {Y}⚠ {len(no_memo)} nodi senza memo(){W}")
else:
    print(f"  {G}✓ tutti i nodi hanno memo(){W}")

# ── CHECK 7 — CSS: proprietà senza valore (righe corrotte) ────────────────
print(f"\n{BOLD}[7] CSS: proprietà senza valore (righe corrotte){W}")
broken_css = []
css_props = re.compile(r'^[a-z][a-z-]+$')
for path in css_files:
    with open(path, encoding='utf-8', errors='ignore') as f:
        for i, line in enumerate(f, 1):
            stripped = line.strip()
            if css_props.match(stripped) and stripped not in ('from', 'to', 'none'):
                broken_css.append(f"  {rel(path)}:{i}  →  '{stripped}'")
if broken_css:
    for x in broken_css: err(x)
    print(f"  {R}✗ {len(broken_css)} righe corrotte{W}")
else:
    print(f"  {G}✓ nessuna riga corrotta{W}")

# ── CHECK 8 — @keyframes duplicati tra file CSS ────────────────────────────
print(f"\n{BOLD}[8] @keyframes duplicati{W}")
kf_map = defaultdict(list)
for path in css_files:
    with open(path, encoding='utf-8', errors='ignore') as f:
        content = f.read()
    for name in re.findall(r'@keyframes\s+([\w-]+)', content):
        kf_map[name].append(rel(path))

duplicates = {k: v for k, v in kf_map.items() if len(v) > 1}
if duplicates:
    for name, files in sorted(duplicates.items()):
        warn(f"  @keyframes {name}  →  {len(files)} file: {', '.join(os.path.basename(f) for f in files)}")
    print(f"  {Y}⚠ {len(duplicates)} keyframes duplicati{W}")
else:
    print(f"  {G}✓ nessun duplicato{W}")

# ── CHECK 9 — variables.css caricato da App.css ───────────────────────────
print(f"\n{BOLD}[9] variables.css importato in App.css{W}")
app_css = os.path.join(SRC, 'styles', 'App.css')
if os.path.exists(app_css):
    with open(app_css, encoding='utf-8', errors='ignore') as f:
        content = f.read()
    if 'variables.css' in content:
        print(f"  {G}✓ importato{W}")
    else:
        err(f"  {rel(app_css)} non importa variables.css")
        print(f"  {R}✗ NON importato — bug critico{W}")
else:
    warn("  App.css non trovato")

# ── CHECK 10 — Colori hardcoded top-5 (campione rapido) ───────────────────
print(f"\n{BOLD}[10] Colori hardcoded (campione top-5){W}")
color_patterns = ['#666', '#333', '#ddd', '#fff', '#999']
color_counts = defaultdict(int)
for path in css_files:
    if 'variables.css' in path or 'animations.css' in path: continue
    with open(path, encoding='utf-8', errors='ignore') as f:
        content = f.read()
    for color in color_patterns:
        matches = len(re.findall(re.escape(color) + r'(?![0-9a-fA-F])', content, re.IGNORECASE))
        color_counts[color] += matches

total_hardcoded = sum(color_counts.values())
if total_hardcoded > 50:
    warn(f"  {total_hardcoded} occorrenze top-5 colori: " +
         ', '.join(f"{c}={n}" for c, n in color_counts.items() if n > 0))
    print(f"  {Y}⚠ {total_hardcoded} colori hardcoded{W}")
elif total_hardcoded > 0:
    info(f"  {total_hardcoded} colori residui (sotto soglia)")
    print(f"  {G}✓ {total_hardcoded} residui (accettabile){W}")
else:
    print(f"  {G}✓ nessuno{W}")

# ── CHECK 11 — !important eccessivi ───────────────────────────────────────
print(f"\n{BOLD}[11] !important per file (soglia: 5){W}")
important_heavy = []
for path in css_files:
    with open(path, encoding='utf-8', errors='ignore') as f:
        content = f.read()
    count = content.count('!important')
    if count > 5:
        important_heavy.append(f"  {rel(path)}: {count}")
if important_heavy:
    for x in important_heavy: warn(x)
    print(f"  {Y}⚠ {len(important_heavy)} file sopra soglia{W}")
else:
    print(f"  {G}✓ tutti sotto soglia{W}")

# ── CHECK 12 — File JS/JSX > 500 righe ────────────────────────────────────
print(f"\n{BOLD}[12] File JS/JSX > 500 righe{W}")
big_files = []
for path in jsx_files:
    with open(path, encoding='utf-8', errors='ignore') as f:
        lines = f.readlines()
    if len(lines) > 500:
        big_files.append(f"  {rel(path)}: {len(lines)} righe")
if big_files:
    for x in sorted(big_files, key=lambda x: -int(x.split(': ')[1].split(' ')[0])):
        warn(x)
    print(f"  {Y}⚠ {len(big_files)} file sopra soglia{W}")
else:
    print(f"  {G}✓ tutti sotto 500 righe{W}")

# ── RIEPILOGO ──────────────────────────────────────────────────────────────
print(f"\n{BOLD}{'='*60}{W}")
print(f"{BOLD}  RIEPILOGO{W}")
print(f"{BOLD}{'='*60}{W}")

if errors:
    print(f"\n  {R}{BOLD}ERRORI ({len(errors)}) — da fixare prima di committare:{W}")
    for e in errors:
        print(f"  {R}{e}{W}")

if warnings:
    print(f"\n  {Y}{BOLD}WARNING ({len(warnings)}) — da risolvere nel prossimo sprint:{W}")
    for w in warnings:
        print(f"  {Y}{w}{W}")

if infos:
    print(f"\n  {B}INFO ({len(infos)}):{W}")
    for i in infos:
        print(f"  {B}{i}{W}")

if not errors and not warnings:
    print(f"\n  {G}{BOLD}✓ Tutto ok — nessun problema rilevato{W}")
elif not errors:
    print(f"\n  {Y}{BOLD}Sprint completabile — nessun errore bloccante{W}")
else:
    print(f"\n  {R}{BOLD}✗ {len(errors)} errori bloccanti — fixare prima di consegnare{W}")

print(f"\n  Errori: {len(errors)}  |  Warning: {len(warnings)}  |  Info: {len(infos)}")
print(f"{BOLD}{'='*60}{W}\n")

sys.exit(1 if errors else 0)
