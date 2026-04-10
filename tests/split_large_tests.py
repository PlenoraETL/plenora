"""
split_large_tests.py — Splitta automaticamente i file test > MAX_SIZE_KB
in parti più piccole, dividendo per classi di test.

Uso:
    python split_large_tests.py          # dry-run (mostra cosa farebbe)
    python split_large_tests.py --apply  # esegue lo split
"""

import os
import re
import sys
from pathlib import Path

MAX_SIZE_BYTES = 20_000  # 20 KB
BASE_DIR = Path(__file__).parent


def find_large_test_files():
    """Trova tutti i file test_*.py > MAX_SIZE nelle directory unit/."""
    large = []
    for root, dirs, files in os.walk(BASE_DIR):
        if "unit" not in root:
            continue
        for f in sorted(files):
            if f.startswith("test_") and f.endswith(".py"):
                path = Path(root) / f
                size = path.stat().st_size
                if size > MAX_SIZE_BYTES:
                    large.append((path, size))
    return large


def parse_file(path):
    """Parsa un file test in: header (imports/fixtures/helpers) + lista di classi."""
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)

    # Trova tutte le posizioni di "class Test..."
    class_starts = []
    for i, line in enumerate(lines):
        if re.match(r'^class\s+Test', line):
            class_starts.append(i)

    if not class_starts:
        return None  # no classes, skip

    # Header = tutto prima della prima classe
    # Ma cerchiamo anche commenti di sezione (# ===) prima di ogni classe
    header_end = class_starts[0]
    # Include section comments that belong to the first class
    while header_end > 0 and (
        lines[header_end - 1].strip() == "" or
        lines[header_end - 1].strip().startswith("#")
    ):
        header_end -= 1
        # Ma non mangiare l'header vero (imports, fixtures, ecc.)
        if header_end <= 0:
            break

    header_lines = lines[:header_end]

    # Per ogni classe, includi i commenti di sezione sopra di essa
    classes = []
    for idx, start in enumerate(class_starts):
        # Cerca inizio sezione (commenti # === sopra la classe)
        section_start = start
        while section_start > 0 and (
            lines[section_start - 1].strip() == "" or
            lines[section_start - 1].strip().startswith("#")
        ):
            # Non risalire oltre la fine della classe precedente
            if idx > 0 and section_start - 1 < class_starts[idx - 1]:
                break
            section_start -= 1

        # Fine della classe = inizio sezione successiva o fine file
        if idx + 1 < len(class_starts):
            # Fine = inizio sezione commenti della prossima classe
            next_section = class_starts[idx + 1]
            while next_section > start and (
                lines[next_section - 1].strip() == "" or
                lines[next_section - 1].strip().startswith("#")
            ):
                if next_section - 1 <= start:
                    break
                next_section -= 1
            end = next_section
        else:
            end = len(lines)

        # Usa section_start solo se è dopo header_end
        actual_start = max(section_start, header_end)
        class_text = lines[actual_start:end]
        class_name = re.match(r'^class\s+(\w+)', lines[start]).group(1)
        classes.append({
            "name": class_name,
            "lines": class_text,
            "size": sum(len(l.encode("utf-8")) for l in class_text),
        })

    header_text = "".join(header_lines)
    header_size = len(header_text.encode("utf-8"))

    return {
        "header": header_lines,
        "header_size": header_size,
        "classes": classes,
    }


def plan_splits(parsed, max_size):
    """Raggruppa le classi in parti, ognuna < max_size."""
    header_size = parsed["header_size"]
    parts = []
    current_classes = []
    current_size = header_size

    for cls in parsed["classes"]:
        cls_size = cls["size"]
        if current_classes and (current_size + cls_size) > max_size:
            parts.append(current_classes)
            current_classes = [cls]
            current_size = header_size + cls_size
        else:
            current_classes.append(cls)
            current_size += cls_size

    if current_classes:
        parts.append(current_classes)

    return parts


def write_parts(path, parsed, parts, apply=False):
    """Scrive i file splittati."""
    stem = path.stem
    # Evita nomi come _part1_part1: rimuovi _partN esistente
    stem = re.sub(r'_part\d+$', '', stem)
    suffix = path.suffix
    parent = path.parent

    results = []
    for i, part_classes in enumerate(parts, 1):
        part_name = f"{stem}_part{i}{suffix}"
        part_path = parent / part_name

        content_lines = list(parsed["header"])
        # Assicura una riga vuota tra header e classi
        if content_lines and content_lines[-1].strip():
            content_lines.append("\n")
        content_lines.append("\n")

        for cls in part_classes:
            content_lines.extend(cls["lines"])

        content = "".join(content_lines)
        size = len(content.encode("utf-8"))
        class_names = [c["name"] for c in part_classes]
        results.append((part_path, size, class_names))

        if apply:
            part_path.write_text(content, encoding="utf-8")

    if apply:
        path.unlink()

    return results


def main():
    apply = "--apply" in sys.argv
    if not apply:
        print("=== DRY RUN (usa --apply per eseguire) ===\n")

    large_files = find_large_test_files()
    if not large_files:
        print("Nessun file test > 20 KB trovato.")
        return

    print(f"File da splittare: {len(large_files)}\n")
    total_original = 0
    total_parts = 0

    for path, size in large_files:
        rel = path.relative_to(BASE_DIR)
        parsed = parse_file(path)
        if parsed is None:
            print(f"  SKIP {rel} — nessuna classe trovata")
            continue

        parts = plan_splits(parsed, MAX_SIZE_BYTES)
        if len(parts) <= 1:
            print(f"  SKIP {rel} ({size // 1024} KB) — non splittabile (una sola parte)")
            continue

        total_original += 1
        total_parts += len(parts)

        print(f"  {rel} ({size // 1024} KB, {len(parsed['classes'])} classi) -> {len(parts)} parti:")
        results = write_parts(path, parsed, parts, apply=apply)
        for part_path, part_size, class_names in results:
            part_rel = part_path.relative_to(BASE_DIR)
            classes_str = ", ".join(class_names[:3])
            if len(class_names) > 3:
                classes_str += f", +{len(class_names) - 3}"
            print(f"    {part_rel.name} ({part_size // 1024} KB): {classes_str}")
        print()

    print(f"\nTotale: {total_original} file -> {total_parts} parti")
    if not apply:
        print("\nRilancia con --apply per eseguire lo split.")
    else:
        print("\nSplit completato!")


if __name__ == "__main__":
    main()
