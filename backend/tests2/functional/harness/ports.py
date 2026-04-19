"""Helper per allocare porte locali libere per server di test."""

from __future__ import annotations

import socket


def find_free_port(host: str = "127.0.0.1") -> int:
    """Ritorna una porta TCP locale libera.

    La porta e' adatta per bootstrap di server di test locali.
    Il caller e' responsabile di avviare il processo subito dopo, per
    ridurre la finestra di race.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind((host, 0))
        return int(sock.getsockname()[1])
