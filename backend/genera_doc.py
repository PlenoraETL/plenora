import os
import ast
import re
import textwrap
from markdown_pdf import MarkdownPdf, Section

def count_lines(filepath):
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            return sum(1 for line in f)
    except: return 0

def get_file_summary(filepath, max_lines=3, width=60):
    """Estrae la docstring e la formatta con ritorni a capo per il PDF."""
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            tree = ast.parse(f.read())
        doc = ast.get_docstring(tree)
        if doc:
            # Pulizia: prendiamo le prime righe significative
            lines = [l.strip() for l in doc.split('\n') if l.strip()]
            text = " ".join(lines[:max_lines])
            # Crea un blocco di testo che va a capo ogni 'width' caratteri
            wrapped = textwrap.wrap(text, width=width)
            return wrapped
    except: pass
    return ["Nessuna descrizione"]

def generate_project_tree(root_dir, indent=""):
    """Genera l'albero inserendo le descrizioni multi-riga allineate."""
    tree_str = ""
    exclude = {'.git', '__pycache__', 'venv', 'docs_md', 'genera_doc.py'}
    items = sorted(os.listdir(root_dir))
    
    for i, item in enumerate(items):
        if item in exclude or item.endswith(('.md', '.pdf')): continue
        
        path = os.path.join(root_dir, item)
        is_last = (i == len(items) - 1)
        prefix = "└── " if is_last else "├── "
        
        if os.path.isdir(path):
            tree_str += f"{indent}{prefix}{item}/\n"
            tree_str += generate_project_tree(path, indent + ("    " if is_last else "│   "))
        elif item.endswith('.py'):
            lines = count_lines(path)
            desc_lines = get_file_summary(path)
            
            # La prima riga ha il nome del file
            name_part = f"{item}"
            tree_str += f"{indent}{prefix}{name_part:<25} # {desc_lines[0]} ({lines} righe)\n"
            
            # Le righe successive della descrizione sono indentate sotto il '#'
            for extra_line in desc_lines[1:]:
                tree_str += f"{indent}{'    ' if is_last else '│   '}{' ':<27} # {extra_line}\n"
            
    return tree_str

def parse_detailed_content(filepath):
    """Analisi dettagliata per il corpo del documento."""
    with open(filepath, "r", encoding="utf-8") as f:
        content = f.read()
        try: tree = ast.parse(content)
        except: return ""
    output = []
    # Rileva le sezioni grafiche come ==================== TITOLO ====================
    sections = re.findall(r"^[ \t]*#[ \t]*={3,}[ \t]*(.*?)[ \t]*={3,}", content, re.MULTILINE)
    if sections:
        output.append("#### 📑 Aree Logiche:\n" + "\n".join([f"- {s}" for s in sections]))
    
    full_doc = ast.get_docstring(tree)
    if full_doc:
        output.append(f"#### 📖 Documentazione Modulo\n```text\n{full_doc}\n```")
    
    for node in tree.body:
        if isinstance(node, (ast.ClassDef, ast.FunctionDef)):
            doc = ast.get_docstring(node)
            if doc:
                label = "📦 Classe" if isinstance(node, ast.ClassDef) else "𝑓 Funzione"
                output.append(f"### {label} `{node.name}`\n{doc.strip()}")
    return "\n\n".join(output)

def main():
    root = "."
    md_file = "DOCUMENTAZIONE_BACKEND.md"
    pdf_file = "DOCUMENTAZIONE_BACKEND.pdf"
    
    print("🛰️ Analisi architettura e generazione mappa...")
    tree_view = generate_project_tree(root)
    
    # Inizializza il contenuto Markdown con stile per il PDF
    md_content = f"# 📚 Documentazione Tecnica PLENORA-ETL\n\n"
    md_content += "## 🏗 Mappa del Progetto\n"
    md_content += f"```text\nbackend/\n{tree_view}```\n\n"
    md_content += '<div style="page-break-after: always;"></div>\n\n'

    # Navigazione ricorsiva per i dettagli
    for r, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in {'.git', '__pycache__', 'venv', 'docs_md'}]
        for file in sorted(files):
            if file.endswith(".py") and not file.startswith("__") and file != "genera_doc.py":
                path = os.path.join(r, file)
                rel_path = os.path.relpath(path, root)
                print(f"📝 Dettagli: {rel_path}")
                
                detail = parse_detailed_content(path)
                if detail:
                    md_content += f"## 🛠 File: `{rel_path}` ({count_lines(path)} righe)\n"
                    md_content += detail + "\n\n"
                    md_content += '<div style="page-break-after: always;"></div>\n\n'

    with open(md_file, "w", encoding="utf-8") as f:
        f.write(md_content)

    print(f"📦 Conversione in PDF (bookmarks inclusi)...")
    try:
        pdf = MarkdownPdf(toc_level=2)
        pdf.add_section(Section(md_content))
        pdf.save(pdf_file)
        print(f"✅ Successo! File pronti:\n- {md_file}\n- {pdf_file}")
    except Exception as e:
        print(f"❌ Errore PDF: {e}")

if __name__ == "__main__":
    main()