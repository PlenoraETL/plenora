"""test_323 - SQL Server ESTRAI to MANIPOLA chain."""

from _cross_db_extended import run_cross_db_scenario


if __name__ == "__main__":
    run_cross_db_scenario("mssql", "manipola_chain")
