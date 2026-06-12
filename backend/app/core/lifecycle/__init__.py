"""Lifecycle cross-domain neutro (Epic E roadmap accoppiamento).

Meccanismi di ciclo di vita che coordinano piu' domini senza appartenere
a nessuno: il registry del group cleanup vive qui, i domini espongono
solo le proprie funzioni di cleanup e il wiring avviene al composition
root (`app/runtime/group_cleanup_wiring.py`).

Vincolo architetturale: questo package non importa altri domini di
`app.core` — e' foundation pura (out-degree zero nel grafo import).
"""
