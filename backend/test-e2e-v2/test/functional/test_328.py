"""test_328 - Cross-db ESTRAI -> CARICA DB -> ESTRAI roundtrip."""

from _cross_db_full import run_scenario


if __name__ == "__main__":
    run_scenario("carica_db_roundtrip")
