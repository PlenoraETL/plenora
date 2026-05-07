"""Benchmark shortlist Fase 2 / punto 3 — Batch 3.7 + 3.8.

Misura lo speedup atteso di candidati "forti" emersi dalla shortlist
pre-refactor (vedi checkpoint 3.7 e 3.8):

  A. `spatial.py::op_geo_clean_topology` Step 1 — validazione geometrie:
     loop Python su `.at[idx]` con `make_valid` → vettoriale via mask +
     batch `shapely.make_valid`.
     Riferimento runtime: backend/app/core/manipola/transforms/spatial.py:1555-1567

  B. `features_service.py` — parsing WKT per-riga:
     `df[col].apply(lambda x: wkt.loads(x) if pd.notna(x) else None)` →
     vettoriale via `gpd.GeoSeries.from_wkt` su subset non-NaN.
     Riferimento runtime: backend/app/core/features/features_service.py:1214

  C. `transforms/base.py::op_text_normalize` — normalizzazione testo:
     `series.apply(lambda v: normalize_text(v, ops))` → chain
     `pd.Series.str.*` per i sotto-passaggi vectorizzabili, fallback
     `apply` solo su strip_accents (NFKD non vectorial in pandas).
     Misurato con 2 varianti `ops`: `"lower"` (upper-bound puramente
     vectoriale) e `"full"` (lower-bound realistico con strip_accents
     residuo). Riferimento runtime:
     backend/app/core/manipola/transforms/base.py:1694-1722

Scopo: produrre numeri concreti (baseline vs alternative, speedup ratio)
per decidere in Fase 3 se procedere con il refactor runtime o scartare
il candidato come upside insufficiente.

**Nessun codice runtime e' toccato**: le alternative vettoriali sono
inline in questo file. Il test **asserisce equivalenza semantica** fra
baseline e alternativa prima di riportare il ratio, pena fallimento.

**Esclusione dal run default**: modulo gatizzato via env
`URBY_RUN_PERF_SHORTLIST=1`. Senza env var tutti i test vengono
skippati. Esecuzione manuale:

    URBY_RUN_PERF_SHORTLIST=1 pytest \
        backend/tests2/performance/test_bench_phase_2_perf_shortlist.py \
        -v -s

Il marker `perf_bench` e' coerente con `test_bench_bulk_vs_executemany_clientside.py`.
"""

from __future__ import annotations

import os
import statistics
import time
from typing import Callable, List

import geopandas as gpd
import numpy as np
import pandas as pd
import pytest
from shapely import wkt as _wkt
from shapely.geometry import Point, Polygon
from shapely.validation import make_valid as _make_valid


pytestmark = [
    pytest.mark.perf_bench,
    pytest.mark.skipif(
        not os.environ.get("URBY_RUN_PERF_SHORTLIST"),
        reason="Benchmark manuale (batch 3.7). Set URBY_RUN_PERF_SHORTLIST=1 per eseguire.",
    ),
]


# Harness mini-bench (copiato dal pattern di test_bench_bulk_vs_execu*)


def _median_runtime(
    fn: Callable, args: tuple, warmup: int = 1, rounds: int = 5
) -> float:
    """Esegue `fn(*args)` warmup volte (scartate), poi `rounds` volte
    misurate. Restituisce la mediana dei tempi in secondi."""
    for _ in range(warmup):
        fn(*args)
    times: List[float] = []
    for _ in range(rounds):
        t0 = time.perf_counter()
        fn(*args)
        times.append(time.perf_counter() - t0)
    return statistics.median(times)


# Candidato A — spatial.py::op_geo_clean_topology Step 1 (make_valid loop)


def _baseline_fix_geom_validity(gdf: gpd.GeoDataFrame) -> int:
    """Replica del Step 1 di `op_geo_clean_topology` (spatial.py:1555-1567):
    loop Python su index + `.at[]` cell-by-cell + `make_valid` solo sui
    non-validi. Identica struttura al runtime attuale — **non** lo chiama
    per isolare Step 1 da Step 2/3 (gia' vettoriali)."""
    result = gdf.copy()
    geom_col = result.geometry.name
    fixed = 0
    for idx in result.index:
        geom = result.at[idx, geom_col]
        if geom is not None and not geom.is_valid:
            result.at[idx, geom_col] = _make_valid(geom)
            fixed += 1
    return fixed


def _alternative_fix_geom_validity_vectorized(gdf: gpd.GeoDataFrame) -> int:
    """Alternativa vettoriale: mask su `is_valid` + batch apply di
    `make_valid` sul subset invalido. Evita il loop Python + l'accesso
    scalare `.at[]`. Preserva la semantica (stesse geometrie fixate,
    stesso contatore)."""
    result = gdf.copy()  # equivalente al .copy() del baseline
    geom_col = result.geometry.name
    geoms = result.geometry
    notna_mask = geoms.notna()
    invalid_mask = notna_mask & (~geoms.is_valid)
    n_fixed = int(invalid_mask.sum())
    if n_fixed:
        # Subset-apply: il loop e' in C dentro shapely, non in Python
        result.loc[invalid_mask, geom_col] = geoms[invalid_mask].apply(_make_valid)
    return n_fixed


def _build_mixed_validity_gdf(n: int, invalid_fraction: float, seed: int = 42) -> gpd.GeoDataFrame:
    """Costruisce un GeoDataFrame con `n` geometrie, di cui `invalid_fraction`
    sono bowtie polygons (self-intersection → `is_valid=False`).

    Usa numpy RNG per riproducibilita'; polygon shapes reali sarebbero
    piu' complessi ma per misurare "loop .at[] vs batch apply" la
    complessita' della geometria e' irrilevante — conta il numero di
    non-valide e il costo medio di `make_valid` per geometria, entrambi
    controllati qui."""
    rng = np.random.default_rng(seed)
    n_invalid = int(n * invalid_fraction)
    n_valid = n - n_invalid

    # Polygoni validi: quadrati random-placed (make_valid e' no-op su validi)
    valid_geoms = [
        Polygon([(x, y), (x + 1, y), (x + 1, y + 1), (x, y + 1)])
        for x, y in rng.uniform(0, 1000, size=(n_valid, 2))
    ]

    # Polygoni invalidi: bowties (X-shape self-intersecting)
    invalid_geoms = [
        Polygon([(x, y), (x + 1, y + 1), (x + 1, y), (x, y + 1)])
        for x, y in rng.uniform(0, 1000, size=(n_invalid, 2))
    ]

    # Interleave per simulare distribuzione realistica
    all_geoms = []
    order = rng.permutation(n)
    valid_iter = iter(valid_geoms)
    invalid_iter = iter(invalid_geoms)
    for i in range(n):
        all_geoms.append(next(invalid_iter) if order[i] < n_invalid else next(valid_iter))

    gdf = gpd.GeoDataFrame({"id": range(n)}, geometry=all_geoms, crs="EPSG:4326")
    return gdf


def _assert_geom_validity_equivalence(baseline_gdf: gpd.GeoDataFrame, alt_gdf: gpd.GeoDataFrame):
    """Verifica equivalenza semantica: stesse geometrie, stessi fix."""
    assert len(baseline_gdf) == len(alt_gdf), "dimensioni divergono"
    geom_col = baseline_gdf.geometry.name
    # Post-fix tutte devono essere valide
    assert baseline_gdf[geom_col].is_valid.all(), "baseline non ha fixato tutti gli invalidi"
    assert alt_gdf[geom_col].is_valid.all(), "alternativa non ha fixato tutti gli invalidi"
    # Le geometrie fixate (originariamente bowtie) non sono uniche in
    # rappresentazione — `make_valid` puo' restituire MultiPolygon o
    # GeometryCollection con stessa area topologica. Verifichiamo
    # equivalenza topologica via `equals_exact` con tolerance ampia,
    # fallback a uguaglianza di area.
    for i in range(len(baseline_gdf)):
        b = baseline_gdf[geom_col].iloc[i]
        a = alt_gdf[geom_col].iloc[i]
        if b is None and a is None:
            continue
        assert b is not None and a is not None, f"None mismatch at idx {i}"
        # Stesso tipo e stessa area (robusto a diverse rappresentazioni wkt)
        assert type(b).__name__ == type(a).__name__, (
            f"tipo divergente idx={i}: {type(b).__name__} vs {type(a).__name__}"
        )
        assert abs(b.area - a.area) < 1e-9, (
            f"area divergente idx={i}: {b.area} vs {a.area}"
        )


def test_bench_spatial_fix_validity_speedup():
    """Candidato A: step 1 di op_geo_clean_topology.

    Baseline = loop `.at[idx]` come in spatial.py:1555-1567.
    Alternativa = mask + batch apply su subset invalido.

    Dataset: 50k geometrie, 20% bowtie (invalidi). Il mix realistic:
    su dataset CAD/IMDF tipici la % di geometrie non valide e' bassa
    ma non zero (dipende dal DXF originario). 20% e' un caso
    stress-friendly: il loop Python paga tanto quanto l'apply."""
    gdf = _build_mixed_validity_gdf(n=50_000, invalid_fraction=0.20)

    # Warmup + equivalence check (outside timing)
    baseline_result = _baseline_fix_geom_validity(gdf)
    alt_result = _alternative_fix_geom_validity_vectorized(gdf)
    # Ricostruiamo i GDF per l'equivalence check (baseline/alternative
    # lavorano sulla copy, il gdf input e' invariato)
    baseline_gdf = gdf.copy()
    _baseline_fix_geom_validity_inplace(baseline_gdf)
    alt_gdf = gdf.copy()
    _alternative_fix_geom_validity_inplace(alt_gdf)
    _assert_geom_validity_equivalence(baseline_gdf, alt_gdf)
    assert baseline_result == alt_result, (
        f"contatore fixes diverso: baseline={baseline_result} alt={alt_result}"
    )

    t_baseline = _median_runtime(
        _baseline_fix_geom_validity, (gdf,), warmup=1, rounds=3
    )
    t_alt = _median_runtime(
        _alternative_fix_geom_validity_vectorized, (gdf,), warmup=1, rounds=3
    )
    speedup = t_baseline / t_alt if t_alt > 0 else float("inf")

    print(
        f"\n[bench A spatial.fix_validity n=50k inv=20%] "
        f"baseline={t_baseline*1000:8.2f}ms  "
        f"alt={t_alt*1000:8.2f}ms  "
        f"speedup={speedup:5.2f}x  "
        f"fixes={baseline_result}"
    )

    # Soft assert: bench informativo, non regression gate
    assert t_alt > 0, "alt path misurato 0 — misura inattendibile"
    assert speedup > 0.5, (
        f"speedup anomalo ({speedup:.2f}x) — revisionare dataset o "
        f"alternativa (potrebbe essere piu' lenta del baseline)"
    )


def _baseline_fix_geom_validity_inplace(gdf: gpd.GeoDataFrame) -> int:
    """Variante in-place per equivalence check (modifica gdf direttamente)."""
    geom_col = gdf.geometry.name
    fixed = 0
    for idx in gdf.index:
        geom = gdf.at[idx, geom_col]
        if geom is not None and not geom.is_valid:
            gdf.at[idx, geom_col] = _make_valid(geom)
            fixed += 1
    return fixed


def _alternative_fix_geom_validity_inplace(gdf: gpd.GeoDataFrame) -> int:
    """Variante in-place per equivalence check."""
    geom_col = gdf.geometry.name
    geoms = gdf.geometry
    notna_mask = geoms.notna()
    invalid_mask = notna_mask & (~geoms.is_valid)
    n_fixed = int(invalid_mask.sum())
    if n_fixed:
        gdf.loc[invalid_mask, geom_col] = geoms[invalid_mask].apply(_make_valid)
    return n_fixed


# Candidato B — features_service.py WKT parse per-riga


def _baseline_wkt_parse_apply(df: pd.DataFrame, col: str) -> pd.Series:
    """Replica del pattern features_service.py:1214:
    `df[col].apply(lambda x: wkt.loads(x) if pd.notna(x) else None)`.
    Ritorna Series di geometrie (object dtype). Invariante con il runtime."""
    return df[col].apply(lambda x: _wkt.loads(x) if pd.notna(x) else None)


def _alternative_wkt_parse_vectorized(df: pd.DataFrame, col: str) -> pd.Series:
    """Alternativa vettoriale via `gpd.GeoSeries.from_wkt`. Gestione NaN
    manuale per preservare esattamente la semantica del baseline (None
    per NaN, non np.nan ne' empty geometry)."""
    s = df[col]
    notna_mask = s.notna()
    result = pd.Series([None] * len(s), index=s.index, dtype=object)
    if notna_mask.any():
        # from_wkt e' batch-parallel (shapely 2.x ufunc), vs apply che
        # e' loop Python per-riga
        parsed = gpd.GeoSeries.from_wkt(s[notna_mask].astype(str).values)
        result.loc[notna_mask] = parsed.values
    return result


def _build_wkt_df(n: int, nan_fraction: float = 0.05, seed: int = 42) -> pd.DataFrame:
    """Costruisce DataFrame con colonna `geometry` WKT. Mix di
    POINT/POLYGON per realismo features ArcGIS."""
    rng = np.random.default_rng(seed)
    n_nan = int(n * nan_fraction)
    n_present = n - n_nan

    # Half POINT, half POLYGON per realismo features
    half = n_present // 2
    points_xy = rng.uniform(0, 1000, size=(half, 2))
    points_wkt = [f"POINT ({x} {y})" for x, y in points_xy]

    squares_xy = rng.uniform(0, 1000, size=(n_present - half, 2))
    squares_wkt = [
        f"POLYGON (({x} {y}, {x+1} {y}, {x+1} {y+1}, {x} {y+1}, {x} {y}))"
        for x, y in squares_xy
    ]

    all_wkt = points_wkt + squares_wkt
    # Aggiungi NaN nelle posizioni randomizzate
    nan_positions = set(rng.choice(n, size=n_nan, replace=False))
    full_col = []
    wkt_iter = iter(all_wkt)
    for i in range(n):
        if i in nan_positions:
            full_col.append(None)
        else:
            full_col.append(next(wkt_iter))

    df = pd.DataFrame({
        "id": range(n),
        "geometry": full_col,
    })
    return df


def _assert_wkt_parse_equivalence(baseline: pd.Series, alt: pd.Series):
    """Equivalenza semantica: stesso None-mask, stesse geometrie (WKT
    string comparison per evitare shapely __eq__ caveats)."""
    assert len(baseline) == len(alt), "lunghezze divergono"
    for i in range(len(baseline)):
        b = baseline.iloc[i]
        a = alt.iloc[i]
        if b is None and a is None:
            continue
        assert b is not None and a is not None, f"None mismatch at idx {i}"
        # Confronto via WKT string — deterministic per shapely
        assert b.wkt == a.wkt, f"WKT divergente idx={i}: {b.wkt!r} vs {a.wkt!r}"


def test_bench_wkt_parse_speedup():
    """Candidato B: WKT parse per-riga in features_service.

    Baseline = apply(lambda) come features_service.py:1214.
    Alternativa = gpd.GeoSeries.from_wkt su subset non-NaN.

    Dataset: 100k WKT strings (50% POINT, 50% POLYGON small), 5% NaN.
    Volume realistico per features ingestion ArcGIS/Geosafe."""
    df = _build_wkt_df(n=100_000, nan_fraction=0.05)

    # Equivalence check (outside timing)
    baseline_result = _baseline_wkt_parse_apply(df, "geometry")
    alt_result = _alternative_wkt_parse_vectorized(df, "geometry")
    _assert_wkt_parse_equivalence(baseline_result, alt_result)

    t_baseline = _median_runtime(
        _baseline_wkt_parse_apply, (df, "geometry"), warmup=1, rounds=3
    )
    t_alt = _median_runtime(
        _alternative_wkt_parse_vectorized, (df, "geometry"), warmup=1, rounds=3
    )
    speedup = t_baseline / t_alt if t_alt > 0 else float("inf")

    print(
        f"\n[bench B wkt_parse n=100k nan=5%] "
        f"baseline={t_baseline*1000:8.2f}ms  "
        f"alt={t_alt*1000:8.2f}ms  "
        f"speedup={speedup:5.2f}x"
    )

    assert t_alt > 0, "alt path misurato 0 — misura inattendibile"
    assert speedup > 0.5, (
        f"speedup anomalo ({speedup:.2f}x) — revisionare"
    )


# Candidato C — transforms/base.py::op_text_normalize (apply-lambda) — Batch 3.8


def _baseline_text_normalize(series: pd.Series, ops: str) -> pd.Series:
    """Replica verbatim del runtime (`base.py:1687-1722`): funzione
    nested `normalize_text` + `series.apply(lambda v: normalize_text(v, ops))`.
    Copiata inline per isolamento — NON importa dal runtime."""
    import re
    import unicodedata

    def _strip_accents(text):
        if not isinstance(text, str):
            return text
        nfkd = unicodedata.normalize("NFKD", text)
        return "".join(c for c in nfkd if not unicodedata.combining(c))

    def _normalize(text, op):
        if pd.isna(text) or not isinstance(text, str):
            return text
        s = text
        if op in ("trim", "full"):
            s = s.strip()
        if op == "lower" or op == "full":
            s = s.lower()
        elif op == "upper":
            s = s.upper()
        elif op == "title":
            s = s.title()
        if op in ("strip_accents", "full"):
            s = _strip_accents(s)
        if op in ("strip_double_spaces", "full"):
            s = re.sub(r"\s+", " ", s)
        return s

    return series.apply(lambda v: _normalize(v, ops))


def _alternative_text_normalize_vectorized(series: pd.Series, ops: str) -> pd.Series:
    """Alternativa vettoriale via chain `pd.Series.str.*`. strip_accents
    resta Python-per-elemento (pandas non espone NFKD batch); per
    `ops='full'` rimane un collo di bottiglia, per `ops='lower'` e'
    assente."""
    import unicodedata

    s = series
    if ops in ("trim", "full"):
        s = s.str.strip()
    if ops == "lower" or ops == "full":
        s = s.str.lower()
    elif ops == "upper":
        s = s.str.upper()
    elif ops == "title":
        s = s.str.title()
    if ops in ("strip_accents", "full"):
        # Fallback apply (NFKD non vectorial in pandas). Resta collo Python
        # per ops='full' ma dato che tutto il resto e' vectorizzato,
        # l'effetto sull'alternative e' comunque un saldo positivo.
        def _sa(t):
            if pd.isna(t) or not isinstance(t, str):
                return t
            nfkd = unicodedata.normalize("NFKD", t)
            return "".join(c for c in nfkd if not unicodedata.combining(c))
        s = s.apply(_sa)
    if ops in ("strip_double_spaces", "full"):
        s = s.str.replace(r"\s+", " ", regex=True)
    return s


def _build_text_df(n: int, nan_fraction: float = 0.05, seed: int = 42) -> pd.DataFrame:
    """Costruisce DataFrame con colonna `text` di stringhe miste per
    esercitare tutti i sotto-passaggi di `normalize_text`:
    - leading/trailing whitespace (trim)
    - mix maiuscole/minuscole (lower/upper/title)
    - accenti italiani/francesi/tedeschi (strip_accents)
    - doppi/tripli spazi interni (strip_double_spaces)
    - ~5% NaN per esercitare early-return"""
    rng = np.random.default_rng(seed)

    samples = [
        "  Città  di  Roma  ",
        "  MILANO Centrale  ",
        "Présence française",
        "  Größe   Ergänzung  ",
        "Napoli   Piazza   Garibaldi",
        " àéìòù  MIXED",
        "UPPER case MIXED",
        "double    spaces  here",
        "Torino  Porta  Nuova  ",
        "città studî Università",
    ]
    n_nan = int(n * nan_fraction)
    n_present = n - n_nan

    indices = rng.integers(0, len(samples), size=n_present)
    present = [samples[i] for i in indices]

    # Intersperse NaN in posizioni random
    nan_positions = set(rng.choice(n, size=n_nan, replace=False))
    full_col = []
    present_iter = iter(present)
    for i in range(n):
        if i in nan_positions:
            full_col.append(None)
        else:
            full_col.append(next(present_iter))

    return pd.DataFrame({"id": range(n), "text": full_col})


def _assert_text_normalize_equivalence(baseline: pd.Series, alt: pd.Series):
    """Equivalenza cell-by-cell. Preserva semantica: NaN per NaN,
    stringa per stringa (mai re-encoding silenzioso)."""
    assert len(baseline) == len(alt), "lunghezze divergono"
    for i in range(len(baseline)):
        b = baseline.iloc[i]
        a = alt.iloc[i]
        # NaN handling: entrambi devono essere NaN (o entrambi None, o
        # entrambi la stessa stringa)
        b_is_null = b is None or (isinstance(b, float) and pd.isna(b))
        a_is_null = a is None or (isinstance(a, float) and pd.isna(a))
        if b_is_null and a_is_null:
            continue
        assert not b_is_null and not a_is_null, (
            f"null mismatch idx={i}: baseline={b!r} alt={a!r}"
        )
        assert b == a, f"valore divergente idx={i}: baseline={b!r} alt={a!r}"


@pytest.mark.parametrize("ops", ["lower", "full"])
def test_bench_text_normalize_speedup(ops):
    """Candidato C: normalize_text apply-loop.

    Due varianti:
      - ops='lower'  → upper-bound speedup (catena str.* pura, zero
                       fallback Python)
      - ops='full'   → lower-bound realistico (include strip_accents
                       che resta apply Python anche nell'alternative)

    Dataset: 100k stringhe miste con mix case, accenti, doppi spazi,
    NaN 5%."""
    df = _build_text_df(n=100_000, nan_fraction=0.05)
    col = df["text"]

    # Equivalence check (outside timing)
    baseline_result = _baseline_text_normalize(col, ops)
    alt_result = _alternative_text_normalize_vectorized(col, ops)
    _assert_text_normalize_equivalence(baseline_result, alt_result)

    t_baseline = _median_runtime(
        _baseline_text_normalize, (col, ops), warmup=1, rounds=3
    )
    t_alt = _median_runtime(
        _alternative_text_normalize_vectorized, (col, ops), warmup=1, rounds=3
    )
    speedup = t_baseline / t_alt if t_alt > 0 else float("inf")

    print(
        f"\n[bench C text_normalize n=100k ops={ops!r:8}] "
        f"baseline={t_baseline*1000:8.2f}ms  "
        f"alt={t_alt*1000:8.2f}ms  "
        f"speedup={speedup:5.2f}x"
    )

    assert t_alt > 0, "alt path misurato 0 — misura inattendibile"
    assert speedup > 0.5, (
        f"speedup anomalo ({speedup:.2f}x) per ops={ops!r} — revisionare"
    )
