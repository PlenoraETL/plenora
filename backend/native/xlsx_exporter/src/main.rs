use std::env;
use std::error::Error;
use std::fs::File;
use std::io::{self, BufReader, Read};
use std::path::{Path, PathBuf};

use chrono::{DateTime, Datelike, NaiveDate, NaiveDateTime};
use rust_xlsxwriter::{Format, Workbook, Worksheet};
use serde::Deserialize;

const XLSX_MAX_ROWS: usize = 1_048_576;
const XLSX_MAX_COLUMNS: usize = 16_384;

#[derive(Clone, Copy, Debug, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "lowercase")]
enum ColumnType {
    Boolean,
    Date,
    Datetime,
    Decimal,
    Float,
    Integer,
    String,
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct ColumnSchema {
    name: String,
    #[serde(rename = "type")]
    column_type: ColumnType,
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct ExportSchema {
    schema_version: u32,
    sheet_name: String,
    columns: Vec<ColumnSchema>,
    #[serde(default = "default_delimiter")]
    delimiter: char,
    #[serde(default = "default_null_token")]
    null_token: String,
    row_count_hint: Option<usize>,
    temp_dir: Option<PathBuf>,
}

fn default_delimiter() -> char {
    ','
}

fn default_null_token() -> String {
    "\\N".to_owned()
}

fn invalid_data(message: impl Into<String>) -> io::Error {
    io::Error::new(io::ErrorKind::InvalidData, message.into())
}

fn clean_export_text(value: &str) -> String {
    let mut output = String::with_capacity(value.len());
    let mut in_control_run = false;
    for character in value.chars() {
        let is_control = character <= '\u{001f}' || matches!(character, '\u{2028}' | '\u{2029}');
        if is_control {
            if !in_control_run {
                output.push(' ');
                in_control_run = true;
            }
        } else {
            output.push(character);
            in_control_run = false;
        }
    }
    output.trim_end().to_owned()
}

fn normalize_decimal(value: &str) -> Option<String> {
    let value = value.trim();
    if value.is_empty() {
        return None;
    }
    let (negative, unsigned) = match value.as_bytes()[0] {
        b'-' => (true, &value[1..]),
        b'+' => (false, &value[1..]),
        _ => (false, value),
    };
    let (mantissa, exponent) = match unsigned.split_once(['e', 'E']) {
        Some((mantissa, exponent)) => (mantissa, exponent.parse::<i64>().ok()?),
        None => (unsigned, 0),
    };
    let (integer, fraction) = match mantissa.split_once('.') {
        Some(parts) => parts,
        None => (mantissa, ""),
    };
    if integer.is_empty() && fraction.is_empty() {
        return None;
    }
    if !integer
        .chars()
        .chain(fraction.chars())
        .all(|item| item.is_ascii_digit())
    {
        return None;
    }

    let digits = format!("{integer}{fraction}");
    let decimal_position = integer.len() as i64 + exponent;
    if digits.chars().all(|item| item == '0') {
        return Some(if negative { "-0" } else { "0" }.to_owned());
    }
    let mut normalized = if decimal_position <= 0 {
        format!("0.{}{}", "0".repeat((-decimal_position) as usize), digits)
    } else if decimal_position as usize >= digits.len() {
        format!(
            "{}{}",
            digits,
            "0".repeat(decimal_position as usize - digits.len())
        )
    } else {
        let position = decimal_position as usize;
        format!("{}.{}", &digits[..position], &digits[position..])
    };
    if normalized.contains('.') {
        normalized = normalized
            .trim_end_matches('0')
            .trim_end_matches('.')
            .to_owned();
    }
    while normalized.starts_with('0') && normalized.as_bytes().get(1) != Some(&b'.') {
        normalized.remove(0);
    }
    if negative {
        normalized.insert(0, '-');
    }
    Some(normalized)
}

fn parse_boolean(value: &str) -> Option<bool> {
    match value.trim().to_ascii_lowercase().as_str() {
        "true" | "1" | "yes" | "si" => Some(true),
        "false" | "0" | "no" => Some(false),
        _ => None,
    }
}

fn parse_date(value: &str) -> Option<NaiveDate> {
    const FORMATS: [&str; 4] = ["%Y-%m-%d", "%Y/%m/%d", "%d/%m/%Y", "%d-%m-%Y"];
    FORMATS
        .iter()
        .find_map(|format| NaiveDate::parse_from_str(value.trim(), format).ok())
}

fn parse_datetime(value: &str) -> Option<NaiveDateTime> {
    let value = value.trim();
    if let Ok(datetime) = DateTime::parse_from_rfc3339(value) {
        return Some(datetime.naive_utc());
    }
    if let Ok(datetime) = DateTime::parse_from_str(value, "%Y-%m-%d %H:%M:%S%.f%:z") {
        return Some(datetime.naive_utc());
    }
    const FORMATS: [&str; 6] = [
        "%Y-%m-%d %H:%M:%S%.f",
        "%Y-%m-%dT%H:%M:%S%.f",
        "%Y/%m/%d %H:%M:%S%.f",
        "%d/%m/%Y %H:%M:%S%.f",
        "%d-%m-%Y %H:%M:%S%.f",
        "%Y-%m-%d",
    ];
    FORMATS
        .iter()
        .find_map(|format| {
            NaiveDateTime::parse_from_str(value, format)
                .ok()
                .or_else(|| {
                    NaiveDate::parse_from_str(value, format)
                        .ok()
                        .and_then(|date| date.and_hms_opt(0, 0, 0))
                })
        })
        .or_else(|| parse_date(value).and_then(|date| date.and_hms_opt(0, 0, 0)))
}

fn write_value(
    worksheet: &mut Worksheet,
    row: u32,
    column: u16,
    raw_value: &str,
    column_type: ColumnType,
    date_format: &Format,
) -> Result<(), Box<dyn Error>> {
    match column_type {
        ColumnType::String => {
            worksheet.write_string(row, column, clean_export_text(raw_value))?;
        }
        ColumnType::Integer => {
            if let Ok(value) = raw_value.trim().parse::<i64>() {
                worksheet.write(row, column, value)?;
            } else if let Ok(value) = raw_value.trim().parse::<u64>() {
                worksheet.write(row, column, value)?;
            } else {
                return Err(invalid_data(format!("intero non valido: {raw_value}")).into());
            }
        }
        ColumnType::Float => {
            let value = raw_value.trim().parse::<f64>().map_err(|error| {
                invalid_data(format!("float non valido '{raw_value}': {error}"))
            })?;
            if value.is_nan() {
                return Ok(());
            }
            if value.is_infinite() {
                worksheet.write_string(
                    row,
                    column,
                    if value.is_sign_positive() {
                        "inf"
                    } else {
                        "-inf"
                    },
                )?;
            } else {
                worksheet.write_number(row, column, value)?;
            }
        }
        ColumnType::Boolean => {
            let value = parse_boolean(raw_value)
                .ok_or_else(|| invalid_data(format!("booleano non valido: {raw_value}")))?;
            worksheet.write_boolean(row, column, value)?;
        }
        ColumnType::Decimal => {
            let trimmed = raw_value.trim();
            let value = if matches!(
                trimmed.to_ascii_lowercase().as_str(),
                "nan" | "snan" | "infinity" | "+infinity" | "-infinity"
            ) {
                trimmed.to_owned()
            } else {
                normalize_decimal(raw_value)
                    .ok_or_else(|| invalid_data(format!("decimale non valido: {raw_value}")))?
            };
            worksheet.write_string(row, column, value)?;
        }
        ColumnType::Date => {
            if let Some(value) =
                parse_date(raw_value).or_else(|| parse_datetime(raw_value).map(|item| item.date()))
            {
                if value.year() < 1900 {
                    worksheet.write_string(row, column, value.format("%Y-%m-%d").to_string())?;
                } else {
                    worksheet.write_datetime_with_format(row, column, value, date_format)?;
                }
            } else {
                worksheet.write_string(row, column, clean_export_text(raw_value))?;
            }
        }
        ColumnType::Datetime => {
            if let Some(value) = parse_datetime(raw_value) {
                if value.year() < 1900 {
                    worksheet.write_string(
                        row,
                        column,
                        value.format("%Y-%m-%d %H:%M:%S").to_string(),
                    )?;
                } else {
                    worksheet.write_datetime_with_format(row, column, value, date_format)?;
                }
            } else {
                worksheet.write_string(row, column, clean_export_text(raw_value))?;
            }
        }
    }
    Ok(())
}

fn load_schema(path: &Path) -> Result<ExportSchema, Box<dyn Error>> {
    let file = File::open(path)?;
    let schema: ExportSchema = serde_json::from_reader(BufReader::new(file))?;
    if schema.schema_version != 1 {
        return Err(invalid_data(format!(
            "schema_version non supportata: {}",
            schema.schema_version
        ))
        .into());
    }
    if schema.columns.len() > XLSX_MAX_COLUMNS {
        return Err(invalid_data(format!(
            "XLSX supporta al massimo {XLSX_MAX_COLUMNS} colonne"
        ))
        .into());
    }
    if schema.delimiter.len_utf8() != 1 || schema.delimiter as u32 > u8::MAX as u32 {
        return Err(invalid_data("il delimitatore CSV deve essere un byte ASCII").into());
    }
    if let Some(row_count) = schema.row_count_hint {
        if row_count + 1 > XLSX_MAX_ROWS {
            return Err(invalid_data(format!(
                "XLSX supporta al massimo {} righe dati",
                XLSX_MAX_ROWS - 1
            ))
            .into());
        }
    }
    Ok(schema)
}

fn export_csv_reader<R: Read>(
    input: R,
    schema: ExportSchema,
    output: &str,
) -> Result<usize, Box<dyn Error>> {
    let mut reader = csv::ReaderBuilder::new()
        .delimiter(schema.delimiter as u8)
        .from_reader(input);
    let headers = reader.headers()?.clone();
    let expected_headers: Vec<&str> = schema
        .columns
        .iter()
        .map(|column| column.name.as_str())
        .collect();
    if headers.iter().collect::<Vec<_>>() != expected_headers {
        return Err(invalid_data("le intestazioni CSV non coincidono con lo schema").into());
    }

    let date_format = Format::new().set_num_format("yyyy-mm-dd hh:mm:ss");
    let mut workbook = Workbook::new();
    if let Some(temp_dir) = &schema.temp_dir {
        workbook.set_tempdir(temp_dir)?;
    }
    let worksheet = workbook.add_worksheet_with_constant_memory();
    worksheet.set_name(&schema.sheet_name)?;
    for (column, item) in schema.columns.iter().enumerate() {
        worksheet.write_string(0, column as u16, clean_export_text(&item.name))?;
    }

    let mut row_count = 0usize;
    for (offset, record) in reader.records().enumerate() {
        let record = record?;
        if offset + 2 > XLSX_MAX_ROWS {
            return Err(invalid_data(format!(
                "XLSX supporta al massimo {} righe dati",
                XLSX_MAX_ROWS - 1
            ))
            .into());
        }
        if record.len() != schema.columns.len() {
            return Err(invalid_data(format!(
                "riga CSV {}: attese {} colonne, ricevute {}",
                offset + 2,
                schema.columns.len(),
                record.len()
            ))
            .into());
        }
        for (column, (raw_value, column_schema)) in record.iter().zip(&schema.columns).enumerate() {
            if raw_value == schema.null_token {
                continue;
            }
            write_value(
                worksheet,
                (offset + 1) as u32,
                column as u16,
                raw_value,
                column_schema.column_type,
                &date_format,
            )?;
        }
        row_count = offset + 1;
    }

    if output == "-" {
        workbook.save_to_writer(io::stdout())?;
    } else {
        workbook.save(output)?;
    }
    Ok(row_count)
}

fn export_csv(input: &str, schema_path: &Path, output: &str) -> Result<usize, Box<dyn Error>> {
    let schema = load_schema(schema_path)?;
    if input == "-" {
        let stdin = io::stdin();
        export_csv_reader(stdin.lock(), schema, output)
    } else {
        let file = File::open(input)?;
        export_csv_reader(BufReader::new(file), schema, output)
    }
}

fn write_self_test(output: &str) -> Result<(), Box<dyn Error>> {
    let date_format = Format::new().set_num_format("yyyy-mm-dd hh:mm:ss");
    let mut workbook = Workbook::new();
    let worksheet = workbook.add_worksheet_with_constant_memory();
    worksheet.set_name("Smoke")?;
    worksheet.write_row(0, 0, ["id", "literal", "when"])?;
    worksheet.write_number(1, 0, 1.0)?;
    worksheet.write_string(1, 1, "=1+1")?;
    let date = NaiveDate::from_ymd_opt(2026, 7, 21)
        .ok_or_else(|| invalid_data("data smoke non valida"))?;
    worksheet.write_datetime_with_format(1, 2, date, &date_format)?;
    workbook.save(output)?;
    Ok(())
}

fn argument_value(args: &[String], name: &str) -> Result<String, Box<dyn Error>> {
    let position = args
        .iter()
        .position(|value| value == name)
        .ok_or_else(|| invalid_data(format!("argomento mancante: {name}")))?;
    args.get(position + 1)
        .cloned()
        .ok_or_else(|| invalid_data(format!("valore mancante per {name}")).into())
}

fn print_help() {
    eprintln!(
        "plenora-xlsx-exporter {}\n\n\
         Uso:\n  plenora-xlsx-exporter export --input DATA.csv|- --schema SCHEMA.json --output RESULT.xlsx\n  \
         plenora-xlsx-exporter self-test --output RESULT.xlsx\n  plenora-xlsx-exporter --version",
        env!("CARGO_PKG_VERSION")
    );
}

fn run() -> Result<(), Box<dyn Error>> {
    let args: Vec<String> = env::args().collect();
    match args.get(1).map(String::as_str) {
        Some("--version") | Some("-V") => {
            println!("plenora-xlsx-exporter {}", env!("CARGO_PKG_VERSION"));
            Ok(())
        }
        Some("export") => {
            let input = argument_value(&args, "--input")?;
            let schema = argument_value(&args, "--schema")?;
            let output = argument_value(&args, "--output")?;
            let rows = export_csv(&input, Path::new(&schema), &output)?;
            if output != "-" {
                println!("{{\"status\":\"ok\",\"rows\":{rows},\"output\":{output:?}}}");
            }
            Ok(())
        }
        Some("self-test") => {
            let output = argument_value(&args, "--output")?;
            write_self_test(&output)?;
            println!("{{\"status\":\"ok\",\"output\":{output:?}}}");
            Ok(())
        }
        _ => {
            print_help();
            Err(invalid_data("comando non valido").into())
        }
    }
}

fn main() {
    if let Err(error) = run() {
        eprintln!("plenora-xlsx-exporter: {error}");
        std::process::exit(2);
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn text_cleanup_matches_plenora_policy() {
        assert_eq!(clean_export_text("prima\r\nriga   "), "prima riga");
        assert_eq!(clean_export_text("a\u{2028}\u{2029}b"), "a b");
        assert_eq!(clean_export_text("=1+1"), "=1+1");
    }

    #[test]
    fn decimal_normalization_is_lossless_text() {
        assert_eq!(normalize_decimal("123.4500").as_deref(), Some("123.45"));
        assert_eq!(normalize_decimal("0001.2300").as_deref(), Some("1.23"));
        assert_eq!(normalize_decimal("1E+3").as_deref(), Some("1000"));
        assert_eq!(normalize_decimal("1.2E-3").as_deref(), Some("0.0012"));
        assert_eq!(normalize_decimal("-0.00").as_deref(), Some("-0"));
        assert_eq!(normalize_decimal("not-a-number"), None);
    }

    #[test]
    fn temporal_parsing_preserves_italian_and_utc_semantics() {
        assert_eq!(
            parse_date("31/12/2024"),
            NaiveDate::from_ymd_opt(2024, 12, 31)
        );
        assert_eq!(
            parse_datetime("2024-02-03T14:15:16+02:00"),
            NaiveDate::from_ymd_opt(2024, 2, 3).and_then(|date| date.and_hms_opt(12, 15, 16))
        );
    }

    #[test]
    fn booleans_fail_closed() {
        assert_eq!(parse_boolean("TRUE"), Some(true));
        assert_eq!(parse_boolean("0"), Some(false));
        assert_eq!(parse_boolean("forse"), None);
    }

    #[test]
    fn csv_contract_writes_a_valid_xlsx_container() {
        let directory = tempfile::tempdir().expect("temp directory");
        let csv_path = directory.path().join("input.csv");
        let schema_path = directory.path().join("schema.json");
        let output_path = directory.path().join("output.xlsx");
        std::fs::write(
            &csv_path,
            "id,literal,when,amount\n1,=1+1,31/12/2024,001.2300\n",
        )
        .expect("csv fixture");
        std::fs::write(
            &schema_path,
            r#"{
                "schema_version": 1,
                "sheet_name": "Sheet1",
                "columns": [
                    {"name": "id", "type": "integer"},
                    {"name": "literal", "type": "string"},
                    {"name": "when", "type": "date"},
                    {"name": "amount", "type": "decimal"}
                ],
                "row_count_hint": 1
            }"#,
        )
        .expect("schema fixture");

        let rows = export_csv(
            csv_path.to_str().expect("csv path"),
            &schema_path,
            output_path.to_str().expect("output path"),
        )
        .expect("xlsx export");
        let payload = std::fs::read(output_path).expect("xlsx payload");

        assert_eq!(rows, 1);
        assert!(payload.starts_with(b"PK\x03\x04"));
        assert!(payload.len() > 1_000);
    }

    #[test]
    fn csv_stream_contract_writes_a_valid_xlsx_container() {
        let directory = tempfile::tempdir().expect("temp directory");
        let schema_path = directory.path().join("schema.json");
        let output_path = directory.path().join("stream-output.xlsx");
        std::fs::write(
            &schema_path,
            r#"{
                "schema_version": 1,
                "sheet_name": "Sheet1",
                "columns": [
                    {"name": "id", "type": "integer"},
                    {"name": "literal", "type": "string"}
                ],
                "row_count_hint": 1
            }"#,
        )
        .expect("schema fixture");

        let schema = load_schema(&schema_path).expect("schema");
        let rows = export_csv_reader(
            io::Cursor::new(b"id,literal\n1,=1+1\n"),
            schema,
            output_path.to_str().expect("output path"),
        )
        .expect("stream xlsx export");
        let payload = std::fs::read(output_path).expect("xlsx payload");

        assert_eq!(rows, 1);
        assert!(payload.starts_with(b"PK\x03\x04"));
        assert!(payload.len() > 1_000);
    }
}
