# Plenora XLSX exporter

Native XLSX writer used by the optional high-performance export path. It reads
a UTF-8 CSV plus an explicit JSON schema and writes rows sequentially with
`rust_xlsxwriter` constant-memory mode.

The executable is an accelerator, not the source of dataframe semantics. The
Python adapter chooses column types and a collision-resistant null token. If
the executable is unavailable, rejects an input, or sees an unsupported mixed
object column, Plenora retains the Python XlsxWriter fallback.

## CLI

```powershell
plenora-xlsx-exporter.exe export `
  --input - `
  --schema schema.json `
  --output result.xlsx
```

`--input -` legge il CSV UTF-8 da standard input, evitando un file dati
temporaneo. Un percorso normale resta supportato per diagnostica e uso manuale.

Use `--output -` to write the XLSX byte stream to stdout. Diagnostics are
written to stderr, so stdout remains safe for a storage pipe.

Schema example:

```json
{
  "schema_version": 1,
  "sheet_name": "Sheet1",
  "columns": [
    {"name": "id", "type": "integer"},
    {"name": "amount", "type": "decimal"},
    {"name": "when", "type": "datetime"},
    {"name": "description", "type": "string"}
  ],
  "delimiter": ",",
  "null_token": "__PLENORA_NULL_7f99__",
  "row_count_hint": 1000
}
```

Supported types are `boolean`, `date`, `datetime`, `decimal`, `float`,
`integer`, and `string`. Decimal values are stored as normalized text to avoid
Excel precision loss. Invalid declared date/datetime cells stay text, matching
the Python export policy. Invalid numeric and boolean cells fail closed.

## Build

The canonical Windows x64 build is produced by
`.github/workflows/xlsx-exporter-windows.yml`. Release archives contain the
statically linked executable and third-party notices; no Rust installation is
required on the target Windows Server.

For a tagged Plenora release, download the matching `.zip` and `.sha256` assets
and verify them before extraction:

```powershell
$archive = 'plenora-xlsx-exporter-vX.Y.Z-windows-x64.zip'
$expected = ((Get-Content "$archive.sha256") -split '\s+')[0]
$actual = (Get-FileHash -Algorithm SHA256 $archive).Hash.ToLowerInvariant()
if ($actual -ne $expected) { throw 'Checksum XLSX exporter non valido' }

Expand-Archive $archive -DestinationPath C:\plenora\backend\bin -Force
& C:\plenora\backend\bin\plenora-xlsx-exporter.exe --version
```

The release binary uses the static MSVC C runtime. The server does not need
Rust, Cargo, Visual Studio Build Tools, Docker, or additional DLLs. With the
default `PLENORA_XLSX_ENGINE=auto`, placing the executable in `backend/bin`
enables it automatically; `python` disables it and `rust` makes fallbacks
visible as warnings.
