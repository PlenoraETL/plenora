"""test_336 - FTP/SFTP datasource multi-format reads.

Focus expanded perimeter:
- datasource schema endpoint and datasource_node runtime parsing;
- CSV, JSON, Parquet, XLSX and GeoJSON through FTP and SFTP.
"""

from _cross_db_full import run_scenario

run_scenario("datasource_multiformat")
