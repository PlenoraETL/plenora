"""
Grafo degli import cross-domain tra i package di backend/app/core.

Strumento di misura per la roadmap accoppiamento (Epic A): estrae via AST
gli import tra domini, calcola le componenti fortemente connesse (SCC,
Tarjan) e in/out-degree, e produce un report leggibile o una baseline JSON
versionabile.

Solo stdlib: nessun import da app.*, eseguibile ovunque ci sia un Python
recente.

Uso (dalla root backend/):

    python scripts/dependency_graph.py                  # report testuale
    python scripts/dependency_graph.py --format json    # report JSON
    python scripts/dependency_graph.py --write-baseline tests2/shared/config/core_dependency_baseline.json

La guardia "non peggiorare" che consuma la baseline e' in
tests2/shared/test_core_dependency_matrix.py; il documento architetturale
e' docs/architecture/dependency-matrix.md.
"""

from __future__ import annotations

import argparse
import ast
import json
import sys
from pathlib import Path

_CORE_PREFIX = "app.core."


def _iter_domain_modules(core_root: Path) -> list[tuple[str, str, Path]]:
    """Ritorna (dominio, modulo dotted, path) per ogni .py nei package
    di primo livello sotto app/core. I file sciolti alla root di app/core
    (api.py, __init__.py) sono wiring, non domini: esclusi."""
    out: list[tuple[str, str, Path]] = []
    for domain_dir in sorted(p for p in core_root.iterdir() if p.is_dir()):
        domain = domain_dir.name
        for py in sorted(domain_dir.rglob("*.py")):
            rel = py.relative_to(core_root.parent.parent)  # relativo a backend/
            parts = list(rel.with_suffix("").parts)
            if parts[-1] == "__init__":
                parts = parts[:-1]
            out.append((domain, ".".join(parts), py))
    return out


def _resolve_relative(module_dotted: str, is_package: bool, node: ast.ImportFrom) -> list[str]:
    """Risolve un import relativo nel modulo dotted assoluto importato."""
    parts = module_dotted.split(".")
    # package di appartenenza: il modulo stesso se __init__, altrimenti il padre
    package_parts = parts if is_package else parts[:-1]
    if node.level > len(package_parts):
        return []
    base = package_parts[: len(package_parts) - (node.level - 1)]
    if node.module:
        return [".".join(base + node.module.split("."))]
    # from .. import <name>: ogni name puo' essere un sottomodulo
    return [".".join(base + [alias.name]) for alias in node.names]


def _imported_modules(tree: ast.Module, module_dotted: str, is_package: bool) -> list[tuple[str, bool]]:
    """Ritorna (modulo importato, top_level) per ogni import del file.
    top_level=False copre import lazy dentro funzioni e blocchi guarded
    (es. TYPE_CHECKING)."""
    top_level_nodes = set(map(id, tree.body))
    out: list[tuple[str, bool]] = []
    for node in ast.walk(tree):
        is_top = id(node) in top_level_nodes
        if isinstance(node, ast.Import):
            out.extend((alias.name, is_top) for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0:
                if node.module:
                    out.append((node.module, is_top))
            else:
                out.extend((m, is_top) for m in _resolve_relative(module_dotted, is_package, node))
    return out


def build_graph(core_root: Path) -> dict:
    """Costruisce il grafo dominio->dominio.

    Ritorna {"domains": [...], "edges": {"X -> Y": {"files": n, "lazy_only": bool}}}.
    """
    domains: set[str] = set()
    # (src, dst) -> {"files": set[str], "has_top_level": bool}
    raw_edges: dict[tuple[str, str], dict] = {}

    for domain, module_dotted, py in _iter_domain_modules(core_root):
        domains.add(domain)
        is_package = py.name == "__init__.py"
        tree = ast.parse(py.read_text(encoding="utf-8"))
        for imported, is_top in _imported_modules(tree, module_dotted, is_package):
            if not imported.startswith(_CORE_PREFIX):
                continue
            target = imported[len(_CORE_PREFIX):].split(".")[0]
            if not target or target == domain:
                continue
            entry = raw_edges.setdefault(
                (domain, target), {"files": set(), "has_top_level": False}
            )
            entry["files"].add(module_dotted)
            entry["has_top_level"] = entry["has_top_level"] or is_top

    # i target devono essere domini reali (directory), non moduli sciolti
    edges = {
        f"{src} -> {dst}": {
            "files": len(data["files"]),
            "lazy_only": not data["has_top_level"],
        }
        for (src, dst), data in sorted(raw_edges.items())
        if dst in domains
    }
    return {"domains": sorted(domains), "edges": edges}


def strongly_connected_components(domains: list[str], edges: dict) -> list[list[str]]:
    """Tarjan. Ritorna le SCC ordinate per dimensione decrescente, poi alfabetico."""
    adj: dict[str, list[str]] = {d: [] for d in domains}
    for key in edges:
        src, dst = key.split(" -> ")
        adj[src].append(dst)

    index_of: dict[str, int] = {}
    lowlink: dict[str, int] = {}
    on_stack: set[str] = set()
    stack: list[str] = []
    sccs: list[list[str]] = []
    counter = [0]

    def strongconnect(v: str) -> None:
        index_of[v] = lowlink[v] = counter[0]
        counter[0] += 1
        stack.append(v)
        on_stack.add(v)
        for w in adj[v]:
            if w not in index_of:
                strongconnect(w)
                lowlink[v] = min(lowlink[v], lowlink[w])
            elif w in on_stack:
                lowlink[v] = min(lowlink[v], index_of[w])
        if lowlink[v] == index_of[v]:
            comp: list[str] = []
            while True:
                w = stack.pop()
                on_stack.discard(w)
                comp.append(w)
                if w == v:
                    break
            sccs.append(sorted(comp))

    for d in domains:
        if d not in index_of:
            strongconnect(d)
    return sorted(sccs, key=lambda c: (-len(c), c[0]))


def build_report(core_root: Path) -> dict:
    graph = build_graph(core_root)
    domains, edges = graph["domains"], graph["edges"]
    sccs = strongly_connected_components(domains, edges)

    out_degree = {d: 0 for d in domains}
    in_degree = {d: 0 for d in domains}
    for key in edges:
        src, dst = key.split(" -> ")
        out_degree[src] += 1
        in_degree[dst] += 1

    return {
        "domains": domains,
        "edges": edges,
        "sccs": sccs,
        "out_degree": out_degree,
        "in_degree": in_degree,
    }


def render_text(report: dict) -> str:
    lines: list[str] = []
    domains, edges, sccs = report["domains"], report["edges"], report["sccs"]
    lines.append(f"Domini: {len(domains)}   Edge cross-domain: {len(edges)}")
    lines.append("")
    lines.append("SCC (componenti fortemente connesse):")
    for scc in sccs:
        if len(scc) > 1:
            lines.append(f"  [{len(scc)}] {', '.join(scc)}")
    singletons = [c[0] for c in sccs if len(c) == 1]
    lines.append(f"  singoletti ({len(singletons)}): {', '.join(singletons)}")
    lines.append("")
    lines.append("Gradi (out / in):")
    ranked = sorted(domains, key=lambda d: -(report["out_degree"][d] + report["in_degree"][d]))
    for d in ranked:
        lines.append(f"  {d:<22} out={report['out_degree'][d]:<3} in={report['in_degree'][d]}")
    lines.append("")
    lines.append("Edge (file importatori, lazy_only):")
    for key, data in edges.items():
        lazy = "  lazy-only" if data["lazy_only"] else ""
        lines.append(f"  {key:<42} files={data['files']}{lazy}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    parser.add_argument("--format", choices=("text", "json"), default="text")
    parser.add_argument(
        "--write-baseline",
        metavar="PATH",
        help="scrive la baseline JSON (edges + sccs) al path indicato",
    )
    parser.add_argument(
        "--core-root",
        default=str(Path(__file__).resolve().parents[1] / "app" / "core"),
        help="path di app/core (default: relativo a questo script)",
    )
    args = parser.parse_args(argv)

    report = build_report(Path(args.core_root))

    if args.write_baseline:
        baseline = {
            "_comment": (
                "Baseline grafo import cross-domain app/core. Rigenerare con: "
                "python scripts/dependency_graph.py --write-baseline "
                "tests2/shared/config/core_dependency_baseline.json"
            ),
            "edges": {k: v["files"] for k, v in report["edges"].items()},
            "sccs": [c for c in report["sccs"] if len(c) > 1],
        }
        Path(args.write_baseline).write_text(
            json.dumps(baseline, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        print(f"baseline scritta: {args.write_baseline}")
        return 0

    if args.format == "json":
        print(json.dumps(report, indent=2, ensure_ascii=False))
    else:
        print(render_text(report))
    return 0


if __name__ == "__main__":
    sys.exit(main())
