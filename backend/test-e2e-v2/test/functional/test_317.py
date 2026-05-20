"""test_317 - SQL Server cross-db NULL/date/text edge cases."""

from _cross_db_extended import run_cross_db_scenario


if __name__ == "__main__":
    run_cross_db_scenario("mssql", "edge_cases")
