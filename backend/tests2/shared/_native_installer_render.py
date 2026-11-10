"""Rende i file che ``scripts/install-native.ps1`` genera (Caddyfile, custom.ini
di Grafana) a partire dalle here-string dell'installer, senza PowerShell.

Le here-string ``@"..."@`` espandono solo variabili ``$nome``: le si sostituisce
con valori di prova e una variabile sconosciuta e' un errore, cosi' un nuovo
``$qualcosa`` nel template obbliga a dichiararlo qui (e a pensare ai test).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
INSTALLER = ROOT / "scripts" / "install-native.ps1"

_VAR = re.compile(r"\$([A-Za-z_][A-Za-z0-9_]*)")


def installer_text() -> str:
    return INSTALLER.read_text(encoding="utf-8-sig").replace("\r\n", "\n")


def here_string(text: str, first_line: str) -> str:
    """Corpo della here-string ``@"`` la cui prima riga e' ``first_line``."""
    start = text.index('@"\n' + first_line) + 3
    end = text.index('\n"@', start)
    return text[start:end]


def expand(template: str, values: dict[str, str]) -> str:
    def sub(match: re.Match[str]) -> str:
        name = match.group(1)
        if name not in values:
            raise KeyError(f"variabile PowerShell non dichiarata nel test: ${name}")
        return values[name]

    return _VAR.sub(sub, template)


CADDY_VALUES = {
    "FrontendPort": "80",
    "headersCaddy": "C:/plenora-services/plenora-caddy/security-headers.caddy",
    "grafanaHeadersCaddy": "C:/plenora-services/plenora-caddy/grafana-headers.caddy",
    "distDir": "C:/plenora/frontend/dist",
    "GrafanaPort": "3300",
}


def render_caddyfile(*, grafana: bool, values: dict[str, str] | None = None) -> str:
    """Il Caddyfile che l'installer scrive, con o senza -InstallGrafana."""
    text = installer_text()
    vals = {**CADDY_VALUES, **(values or {})}
    route = expand(here_string(text, "    @grafana path /grafana /grafana/*"), vals) if grafana else ""
    return expand(here_string(text, ":$FrontendPort {"), {**vals, "grafanaRoute": route}) + "\n"


GRAFANA_INI_VALUES = {
    "grafanaDataIni": "C:/plenora-data/grafana",
    "grafanaLogsIni": "C:/plenora-services/plenora-grafana/log",
    "grafanaPluginsIni": "C:/plenora-services/plenora-grafana/plugins",
    "GrafanaPort": "3300",
    "grafanaRoot": "http://localhost/grafana/",
    "grafanaAdminLogin": "plenora-grafana-admin-abcdefgh12345678",
    "grafanaSecretKey": "S" * 48,
    "grafanaAdminPassword": "P" * 48,
    "grafanaCookieSecure": "false",
}


def render_grafana_ini(values: dict[str, str] | None = None) -> str:
    text = installer_text()
    template = here_string(text, "; Generato da scripts/install-native.ps1 (-InstallGrafana): non modificare a mano.")
    return expand(template, {**GRAFANA_INI_VALUES, **(values or {})}) + "\n"


# ── Caddyfile come albero di blocchi ─────────────────────────────────────────


@dataclass
class Block:
    """Una direttiva; ``children`` se apre un blocco ``{ ... }``."""

    line: str
    children: list["Block"] = field(default_factory=list)

    @property
    def name(self) -> str:
        return self.line.split()[0] if self.line else ""

    def walk(self, ancestors: tuple["Block", ...] = ()):
        yield self, ancestors
        for child in self.children:
            yield from child.walk((*ancestors, self))


def parse_caddyfile(text: str) -> Block:
    root = Block("")
    stack = [root]
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip() if not raw.strip().startswith("#") else ""
        if not line:
            continue
        if line == "}":
            stack.pop()
            continue
        opens = line.endswith("{")
        block = Block(line[:-1].strip() if opens else line)
        stack[-1].children.append(block)
        if opens:
            stack.append(block)
    assert len(stack) == 1, "parentesi graffe non bilanciate nel Caddyfile"
    return root
