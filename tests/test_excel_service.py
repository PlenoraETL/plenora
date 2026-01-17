"""
Test per ExcelService
"""
import sys
from pathlib import Path

# Aggiungi backend al path per import
backend_path = Path(__file__).parent.parent
sys.path.insert(0, str(backend_path))

from services.excel_service import ExcelService
from models import TableCatalog


def test_parse_excel():
    """Test parsing Excel fixture"""
    print("\n=== TEST: Parse Excel ===")
    
    service = ExcelService()
    fixture_path = Path(r"C:\Users\Marco\Desktop\int_efm\editor_configurazioni\fixtures\ICSCArchibusM_INFORMATION_SCHEMA_COLUMNS.xlsx")
    
    catalog = service.parse_excel(str(fixture_path))
    
    assert isinstance(catalog, TableCatalog)



    assert len(catalog.tables) > 0  # Hai tabelle reali da Archibus
    print(f"DEBUG: Trovate {len(catalog.tables)} tabelle")
    
    # Verifica tabella city
    assert "city" in catalog.tables
    assert "city_id" in catalog.tables["city"]

    
    # Verifica tabella bl
    assert "bl" in catalog.tables

    
    print(f"✓ Tabelle parsate: {list(catalog.tables.keys())}")
    for table, cols in catalog.tables.items():
        print(f"  - {table}: {len(cols)} colonne")
    
    return True


def test_save_load_catalog():
    """Test salvataggio e caricamento catalogo"""
    print("\n=== TEST: Save/Load Catalog ===")
    
    service = ExcelService()
    fixture_path = Path(r"C:\Users\Marco\Desktop\int_efm\editor_configurazioni\fixtures\ICSCArchibusM_INFORMATION_SCHEMA_COLUMNS.xlsx")
    
    # Parse e salva
    catalog = service.parse_excel(str(fixture_path))
    service.save_catalog(catalog)
    
    print(f"✓ Catalogo salvato in: {service.CATALOG_PATH}")
    
    # Ricarica
    loaded_catalog = service.load_catalog()
    
    assert len(loaded_catalog.tables) == len(catalog.tables)
    assert loaded_catalog.tables == catalog.tables
    
    print(f"✓ Catalogo ricaricato correttamente")
    
    return True


def test_get_table_columns():
    """Test get colonne singola tabella"""
    print("\n=== TEST: Get Table Columns ===")
    
    service = ExcelService()
    
    columns = service.get_table_columns("city")
    
    assert len(columns) > 0
    assert "city_id" in columns
    
    print(f"✓ Colonne city: {columns}")
    
    # Test tabella non esistente
    empty = service.get_table_columns("tabella_inesistente")
    assert empty == []
    
    print(f"✓ Tabella inesistente ritorna lista vuota")
    
    return True


if __name__ == "__main__":
    print("=" * 60)
    print("TEST EXCEL SERVICE")
    print("=" * 60)
    
    tests = [
        ("Parse Excel", test_parse_excel),
        ("Save/Load Catalog", test_save_load_catalog),
        ("Get Table Columns", test_get_table_columns)
    ]
    
    results = []
    for name, test_func in tests:
        try:
            passed = test_func()
            results.append((name, True, None))
        except Exception as e:
            results.append((name, False, str(e)))
            print(f"✗ ERRORE: {e}")
    
    # Riepilogo
    print("\n" + "=" * 60)
    print("RIEPILOGO TEST")
    print("=" * 60)
    
    for name, passed, error in results:
        if passed:
            print(f"✓ PASS - {name}")
        else:
            print(f"✗ FAIL - {name}")
            print(f"  Errore: {error}")
    
    total = len(results)
    passed = sum(1 for _, p, _ in results if p)
    print(f"\nTotale: {passed}/{total} test passati")
