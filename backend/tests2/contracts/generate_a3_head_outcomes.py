"""Genera ``a3_head_outcomes.json``: esiti del riferimento 3c8609fe sul corpus A3.

La fixture NON si rigenera dal working tree: registra la verità di HEAD.
Procedura usata (dichiarata nel rapporto A3, «Correzione dopo la conferma»):

    git archive 3c8609fe backend | tar -x -C <scratch>/head
    cp tests2/contracts/a3_runtime_corpus.py <scratch>/head/backend/tests2/contracts/
    cp tests2/contracts/generate_a3_head_outcomes.py <scratch>/head/backend/tests2/contracts/
    cd <scratch>/head/backend && python tests2/contracts/generate_a3_head_outcomes.py <dest>

``a3_runtime_corpus`` dipende solo da moduli presenti in entrambi gli alberi.
"""
from __future__ import annotations

import json
import logging
import sys
import warnings
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

FIXTURE_PATH = Path(__file__).with_name("a3_head_outcomes.json")


def main(destination: Path) -> None:
    warnings.filterwarnings("ignore")
    logging.disable(logging.CRITICAL)
    from tests2.contracts.a3_runtime_corpus import outcomes
    data = outcomes()
    destination.write_text(json.dumps(data, indent=0, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"{len(data)} voci -> {destination}")


if __name__ == "__main__":
    main(Path(sys.argv[1]) if len(sys.argv) > 1 else FIXTURE_PATH)
