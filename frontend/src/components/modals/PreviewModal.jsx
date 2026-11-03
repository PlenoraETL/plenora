/**
 * PreviewModal.jsx — Anteprima dati tabulari + mappa (se geometrico)
 *
 * Tab Tabella : tabella paginata con sort + ricerca globale
 * Tab Mappa   : Leaflet (npm `leaflet`), visibile solo se colonne geometry
 * Tab Statistiche : card per colonna con min/max/null
 *
 * Usato da EstraiEditor, ManipolaEditor, ArricchisciEditor.
 */
import React, { useState, useEffect, useRef, memo, useMemo } from 'react';
import L from 'leaflet';
import 'leaflet/dist/leaflet.css';
import ModalWrapper, { ModalButton } from '../common/ModalWrapper';
import PreviewTable from './PreviewTable';
import PermissionRevokedBanner from '../common/PermissionRevokedBanner';
import './PreviewModal.css';
const parseWKB = (hex) => {
  // EWKB/WKB hex string (PostGIS default output)
  try {
    const buf = new Uint8Array(hex.match(/.{1,2}/g).map(b => parseInt(b, 16)));
    let pos = 0;

    const readByte  = () => buf[pos++];
    const readUint32LE = () => { const v = buf[pos]|(buf[pos+1]<<8)|(buf[pos+2]<<16)|(buf[pos+3]<<24); pos+=4; return v>>>0; };
    const readUint32BE = () => { const v = (buf[pos]<<24)|(buf[pos+1]<<16)|(buf[pos+2]<<8)|buf[pos+3]; pos+=4; return v>>>0; };
    const readFloat64LE = () => {
      const dv = new DataView(buf.buffer, buf.byteOffset + pos, 8); pos += 8;
      return dv.getFloat64(0, true);
    };
    const readFloat64BE = () => {
      const dv = new DataView(buf.buffer, buf.byteOffset + pos, 8); pos += 8;
      return dv.getFloat64(0, false);
    };

    let le, readU32, readF64;

    const readGeom = () => {
      le = readByte() === 1;
      readU32  = le ? readUint32LE  : readUint32BE;
      readF64  = le ? readFloat64LE : readFloat64BE;

      let type = readU32();
      const hasZ    = !!(type & 0x80000000);
      const hasSRID = !!(type & 0x20000000);
      type = type & 0x0FFFFFFF;
      if (type > 1000 && type < 2000) type -= 1000; // Z variants
      if (type > 2000 && type < 3000) type -= 2000; // M variants
      if (type > 3000) type -= 3000;                // ZM variants

      if (hasSRID) readU32(); // skip SRID

      const readCoord = () => {
        const x = readF64(), y = readF64();
        if (hasZ) readF64(); // skip Z
        return [x, y];
      };
      const readCoordList = (n) => Array.from({length: n}, readCoord);
      const readRing = () => readCoordList(readU32());

      switch (type) {
        case 1: return { type: 'Point', coordinates: readCoord() };
        case 2: return { type: 'LineString', coordinates: readCoordList(readU32()) };
        case 3: { const n = readU32(); return { type: 'Polygon', coordinates: Array.from({length:n}, readRing) }; }
        case 4: { const n = readU32(); return { type: 'MultiPoint', coordinates: Array.from({length:n}, () => { readByte(); readU32(); return readCoord(); }) }; }
        case 5: { const n = readU32(); return { type: 'MultiLineString', coordinates: Array.from({length:n}, () => { le=readByte()===1; readU32=le?readUint32LE:readUint32BE; readF64=le?readFloat64LE:readFloat64BE; readU32(); return readCoordList(readU32()); }) }; }
        case 6: { const n = readU32(); const polys = []; for(let i=0;i<n;i++){ le=readByte()===1; readU32=le?readUint32LE:readUint32BE; readF64=le?readFloat64LE:readFloat64BE; readU32(); const nr=readU32(); polys.push(Array.from({length:nr},readRing)); } return { type:'MultiPolygon', coordinates: polys }; }
        case 7: { const n = readU32(); return { type:'GeometryCollection', geometries: Array.from({length:n}, readGeom) }; }
        default: return null;
      }
    };

    return readGeom();
  } catch { return null; }
};
const parseWKT = (wkt) => {
  if (!wkt) return null;
  const s = wkt.trim();

  // Strip SRID prefix: SRID=4326;POINT(...)
  const clean = s.replace(/^SRID=\d+;/i, '');

  // GeoJSON passthrough
  if (clean.startsWith('{')) {
    try { return JSON.parse(clean); } catch { return null; }
  }

  const upper = clean.toUpperCase();

  // Tokenizer helpers
  const readCoord = (str) => {
    const parts = str.trim().split(/\s+/).map(Number);
    // Accept 2D or 3D (drop Z)
    return [parts[0], parts[1]];
  };

  const readCoordList = (str) =>
    str.trim().split(/\s*,\s*/).map(readCoord);

  const readRings = (str) => {
    // Split on '),' boundary
    const ringStrs = str.split(/\)\s*,\s*\(/);
    return ringStrs.map(r => readCoordList(r.replace(/[()]/g, '')));
  };

  try {
    if (upper.startsWith('POINT')) {
      const m = clean.match(/POINT\s*Z?\s*\(([^)]+)\)/i);
      if (!m) return null;
      return { type: 'Point', coordinates: readCoord(m[1]) };
    }

    if (upper.startsWith('LINESTRING')) {
      const m = clean.match(/LINESTRING\s*Z?\s*\(([^)]+)\)/i);
      if (!m) return null;
      return { type: 'LineString', coordinates: readCoordList(m[1]) };
    }

    if (upper.startsWith('POLYGON')) {
      const m = clean.match(/POLYGON\s*Z?\s*\((.+)\)\s*$/i);
      if (!m) return null;
      return { type: 'Polygon', coordinates: readRings(m[1]) };
    }

    if (upper.startsWith('MULTIPOINT')) {
      const m = clean.match(/MULTIPOINT\s*Z?\s*\((.+)\)\s*$/i);
      if (!m) return null;
      const pts = m[1].split(/\)\s*,\s*\(/).map(p => readCoord(p.replace(/[()]/g, '')));
      return { type: 'MultiPoint', coordinates: pts };
    }

    if (upper.startsWith('MULTILINESTRING')) {
      const m = clean.match(/MULTILINESTRING\s*Z?\s*\((.+)\)\s*$/i);
      if (!m) return null;
      const lines = m[1].split(/\)\s*,\s*\(/).map(l => readCoordList(l.replace(/[()]/g, '')));
      return { type: 'MultiLineString', coordinates: lines };
    }

    if (upper.startsWith('MULTIPOLYGON')) {
      const m = clean.match(/MULTIPOLYGON\s*Z?\s*\((.+)\)\s*$/i);
      if (!m) return null;
      // Each polygon is wrapped in an extra ()
      const polyStrs = m[1].split(/\)\s*\)\s*,\s*\(\s*\(/).map(p =>
        p.replace(/^\s*\(/, '').replace(/\)\s*$/, '')
      );
      const polys = polyStrs.map(p => readRings(p));
      return { type: 'MultiPolygon', coordinates: polys };
    }

    if (upper.startsWith('GEOMETRYCOLLECTION')) {
      // Basic: return null, too complex for inline
      return null;
    }
  } catch { /* */ }

  return null;
};

/** Ritorna true se il tipo colonna è geometrico */
const isGeoType = (type) =>
  type?.startsWith('geometry') || type?.startsWith('geography') ||
  type === 'geom' || type === 'geometry' ||
  type === 'geojson' || type === 'geo';

/** Estrai tipo geometria leggibile dal valore WKT/WKB */
const extractGeomType = (val) => {
  if (!val) return 'GEOMETRY';
  const str = String(val).toUpperCase();
  for (const t of ['MULTIPOLYGON','POLYGON','MULTILINESTRING','LINESTRING','MULTIPOINT','POINT','GEOMETRYCOLLECTION']) {
    if (str.startsWith(t)) return t;
  }
  return 'GEOMETRY';
};

/** Palette colori per colore-per su mappa */
const PROV_COLORS = ['#4B8BFF','#34C17A','#9B6DFF','#F5A623','#F87171','#2DD4BF',
  '#FBBF24','#A78BFA','#FB7185','#38BDF8','#FB923C','#A3E635'];
const MapTab = memo(({ rows, columns, active }) => {
  const mapRef = useRef(null);
  const leafletMap = useRef(null);
  const geoLayer = useRef(null);
  const tileLayer = useRef(null);
  const [colorBy, setColorBy] = useState('__none__');
  const [basemap, setBasemap] = useState('dark');
  const [featureCount, setFeatureCount] = useState(0);
  const [geomType, setGeomType] = useState('');

  // Colonne numeriche/stringa disponibili per colorare
  const colorCols = useMemo(() =>
    columns.filter(c => !isGeoType(c.type) && c.type !== 'bool'),
  [columns]);

  // Colonna geometry principale
  const geomCol = useMemo(() =>
    columns.find(c => isGeoType(c.type)),
  [columns]);

  // Colonna label (primo varchar non geo)
  const labelCol = useMemo(() =>
    columns.find(c => c.type === 'str' || c.type === 'varchar' || c.type === 'text'),
  [columns]);

  const TILE_URLS = {
    dark: 'https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png',
    osm:  'https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png',
    sat:  'https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}',
  };

  // Colori per feature
  const getColor = (feature, col) => {
    if (col === '__none__') return '#4B8BFF';
    const val = feature.properties[col];
    const type = columns.find(c => c.name === col)?.type;
    if (!val && val !== 0) return '#3F4D68';
    if (type === 'int' || type === 'real' || type === 'float') {
      // gradient blue → green
      const allVals = rows.map(r => r[col]).filter(v => v != null);
      const min = Math.min(...allVals);
      const max = Math.max(...allVals);
      const t = max === min ? 0.5 : (val - min) / (max - min);
      const h = Math.round(210 - t * 120);
      return `hsl(${h}, 75%, 55%)`;
    }
    // categorical
    const cats = [...new Set(rows.map(r => r[col]))];
    const idx = cats.indexOf(val) % PROV_COLORS.length;
    return PROV_COLORS[idx];
  };

  const styleFeature = (feature) => ({
    fillColor: getColor(feature, colorBy),
    fillOpacity: 0.55,
    color: 'rgba(7,9,15,0.5)',
    weight: 0.8,
  });

  // Inizializza mappa quando il tab diventa attivo
  useEffect(() => {
    if (!active || !mapRef.current) return;

    if (leafletMap.current) {
      // Mappa già init: forza ridimensionamento (container era hidden)
      setTimeout(() => {
        leafletMap.current.invalidateSize();
        if (geoLayer.current) {
          try {
            const b = geoLayer.current.getBounds();
            if (b.isValid()) leafletMap.current.fitBounds(b, { padding: [22, 22] });
          } catch { /* */ }
        }
      }, 50);
      return;
    }

    // Aspetta che il container abbia dimensioni reali prima di init
    setTimeout(() => {
      if (!mapRef.current) return;
      const map = L.map(mapRef.current, { zoomControl: false, attributionControl: false });
      map.setView([44.5, 11.3], 6); // default Italy, verrà sovrascritto da fitBounds
      leafletMap.current = map;
      tileLayer.current = L.tileLayer(TILE_URLS.dark, { maxZoom: 19 }).addTo(map);
      setTimeout(() => {
        map.invalidateSize();
        buildGeoLayer(map, colorBy);
      }, 50);
    }, 80);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  // colorBy + buildGeoLayer omessi: bootstrap mappa una sola volta per apertura
  // del tab. Il restyle al cambio colorBy avviene nell'effect dedicato sotto.
  }, [active]);

  const buildGeoLayer = (map, currentColorBy) => {
    if (!geomCol) return;

    if (geoLayer.current) { map.removeLayer(geoLayer.current); geoLayer.current = null; }

    // Converti rows in GeoJSON
    const features = rows
      .filter(r => r[geomCol.name])
      .map(r => {
        let geom = null;
        try {
          const raw = String(r[geomCol.name]).trim();
          // Detect WKB hex: starts with 00 or 01 followed by hex chars, no spaces
          if (/^[0-9a-fA-F]{10,}$/.test(raw)) {
            geom = parseWKB(raw);
          } else {
            geom = parseWKT(raw);
          }
        } catch { /* skip */ }
        if (!geom) return null;
        const props = { ...r };
        delete props[geomCol.name];
        return { type: 'Feature', geometry: geom, properties: props };
      })
      .filter(Boolean);

    if (!features.length) return;
    setFeatureCount(features.length);
    setGeomType(features[0]?.geometry?.type || '');

    const getColorFn = (feature) => getColor(feature, currentColorBy);

    geoLayer.current = L.geoJSON({ type: 'FeatureCollection', features }, {
      style: (f) => ({
        fillColor: getColorFn(f),
        fillOpacity: 0.55,
        color: 'rgba(7,9,15,0.5)',
        weight: 0.8,
      }),
      pointToLayer: (feature, latlng) =>
        L.circleMarker(latlng, {
          radius: 6,
          fillColor: getColorFn(feature),
          fillOpacity: 0.8,
          color: 'rgba(7,9,15,0.6)',
          weight: 1,
        }),
      onEachFeature: (feature, layer) => {
        // Tooltip costruito con nodi DOM e textContent: nomi e valori delle
        // colonne arrivano dal DB e non vanno mai interpretati come HTML.
        const table = document.createElement('table');
        table.className = 'pm-tooltip';
        for (const [k, v] of Object.entries(feature.properties).slice(0, 6)) {
          const tr = table.insertRow();
          const keyCell = tr.insertCell();
          keyCell.className = 'pm-tip-k';
          keyCell.textContent = k;
          const valueCell = tr.insertCell();
          valueCell.className = 'pm-tip-v';
          valueCell.textContent = v ?? 'NULL';
        }
        layer.bindTooltip(table, { sticky: true, opacity: 1, className: 'pm-tooltip-wrap' });
        layer.on('mouseover', function () {
          this.setStyle?.({ fillOpacity: 0.85, weight: 1.5, color: 'rgba(255,255,255,0.3)' });
        });
        layer.on('mouseout', function () { geoLayer.current?.resetStyle?.(this); });
      },
    }).addTo(map);

    try {
      const bounds = geoLayer.current.getBounds();
      if (bounds.isValid()) map.fitBounds(bounds, { padding: [22, 22] });
    } catch { /* empty bounds */ }
  };

  // Aggiorna colori quando cambia colorBy
  useEffect(() => {
    if (!leafletMap.current || !geoLayer.current) return;
    geoLayer.current.setStyle((f) => ({
      fillColor: getColor(f, colorBy),
      fillOpacity: 0.55,
      color: 'rgba(7,9,15,0.5)',
      weight: 0.8,
    }));
  // eslint-disable-next-line react-hooks/exhaustive-deps
  // getColor stabile (pure fn module-level): non serve in deps; vogliamo
  // restyle solo al cambio dell'asse di colorazione.
  }, [colorBy]);

  // Cambia basemap
  const handleBasemap = (name) => {
    if (!leafletMap.current) return;
    setBasemap(name);
    if (tileLayer.current) leafletMap.current.removeLayer(tileLayer.current);
    tileLayer.current = L.tileLayer(TILE_URLS[name], { maxZoom: 19 }).addTo(leafletMap.current);
  };

  const fitBounds = () => {
    if (geoLayer.current && leafletMap.current) {
      try {
      const b = geoLayer.current.getBounds();
      if (b.isValid()) leafletMap.current.fitBounds(b, { padding: [22, 22] });
    } catch { /* */ }
    }
  };

  return (
    <div className="pm-map-tab">
      <div className="pm-map-wrap" ref={mapRef} />

      {/* top chips */}
      <div className="pm-map-top">
        <div className="pm-map-chip">
          <span className="pm-map-chip-dot" />
          {featureCount > 0 ? `${featureCount} features` : rows.length + ' righe'}{geomType ? ` · ${geomType}` : ''}
        </div>

        {colorCols.length > 0 && (
          <div className="pm-map-chip pm-map-colorselector">
            <span>🎨</span>
            <select value={colorBy} onChange={e => setColorBy(e.target.value)}>
              <option value="__none__">— nessun colore —</option>
              {colorCols.map(c => (
                <option key={c.name} value={c.name}>{c.name}</option>
              ))}
            </select>
          </div>
        )}
      </div>

      {/* basemap */}
      <div className="pm-basemap-sw">
        {['dark','osm','sat'].map(b => (
          <button
            key={b}
            type="button"
            className={`pm-basemap-btn${basemap === b ? ' active' : ''}`}
            onClick={() => handleBasemap(b)}
          >
            {b === 'dark' ? 'Dark' : b === 'osm' ? 'OSM' : 'Satellite'}
          </button>
        ))}
      </div>

      {/* zoom controls */}
      <div className="pm-map-controls">
        <button type="button" className="pm-map-ctrl-btn" onClick={() => leafletMap.current?.zoomIn()}>+</button>
        <button type="button" className="pm-map-ctrl-btn" onClick={() => leafletMap.current?.zoomOut()}>−</button>
        <button type="button" className="pm-map-ctrl-btn" title="Fit extent" onClick={fitBounds}>⊡</button>
      </div>
    </div>
  );
});
MapTab.displayName = 'MapTab';
const StatsTab = memo(({ rows, columns }) => {
  const stats = useMemo(() => {
    return columns.map(col => {
      const vals = rows.map(r => r[col.name]);
      const nullCount = vals.filter(v => v == null || v === '').length;
      const nonNull = vals.filter(v => v != null && v !== '');

      if (isGeoType(col.type)) {
        const types = [...new Set(nonNull.map(v => extractGeomType(v)))];
        return { col, nullCount, geomTypes: types };
      }

      if (col.type === 'int' || col.type === 'real' || col.type === 'float') {
        const nums = nonNull.map(Number).filter(n => !isNaN(n));
        const min = nums.length ? Math.min(...nums) : null;
        const max = nums.length ? Math.max(...nums) : null;
        const avg = nums.length ? nums.reduce((a, b) => a + b, 0) / nums.length : null;
        return { col, nullCount, min, max, avg };
      }

      // varchar / str
      const uniq = new Set(nonNull).size;
      const sorted = [...nonNull].sort();
      return { col, nullCount, uniq, min: sorted[0], max: sorted[sorted.length - 1] };
    });
  }, [rows, columns]);

  return (
    <div className="pm-stats-grid">
      {stats.map(s => {
        const pNull = rows.length ? Math.round((s.nullCount / rows.length) * 100) : 0;
        const nullColor = pNull === 0 ? 'var(--plen-ok, #34D399)' : pNull > 5 ? 'var(--plen-err, #F87171)' : 'var(--plen-warn, #FBBF24)';
        const isGeo = isGeoType(s.col.type);
        const isNum = s.col.type === 'int' || s.col.type === 'real' || s.col.type === 'float';

        return (
          <div key={s.col.name} className="pm-stat-card">
            <div className="pm-stat-header">
              <span className="pm-stat-name">{s.col.name}</span>
              <span className={`pm-stat-type ${isGeo ? 'geo' : isNum ? 'num' : 'str'}`}>
                {s.col.type || 'str'}
              </span>
            </div>

            <div className="pm-stat-rows">
              {isGeo ? (
                <>
                  <div className="pm-stat-row">
                    <span className="pm-stat-k">Tipo geom</span>
                    <span className="pm-stat-v ok">{s.geomTypes?.join(', ') || '—'}</span>
                  </div>
                  <div className="pm-stat-row">
                    <span className="pm-stat-k">Null</span>
                    <span className={`pm-stat-v ${s.nullCount === 0 ? 'ok' : 'bad'}`}>{s.nullCount}</span>
                  </div>
                </>
              ) : isNum ? (
                <>
                  <div className="pm-stat-row"><span className="pm-stat-k">Min</span><span className="pm-stat-v">{s.min?.toLocaleString() ?? '—'}</span></div>
                  <div className="pm-stat-row"><span className="pm-stat-k">Max</span><span className="pm-stat-v">{s.max?.toLocaleString() ?? '—'}</span></div>
                  <div className="pm-stat-row"><span className="pm-stat-k">Media</span><span className="pm-stat-v">{s.avg != null ? Number(s.avg.toFixed(2)).toLocaleString() : '—'}</span></div>
                  <div className="pm-stat-row"><span className="pm-stat-k">Null</span><span className={`pm-stat-v ${s.nullCount === 0 ? 'ok' : 'bad'}`}>{s.nullCount}</span></div>
                </>
              ) : (
                <>
                  <div className="pm-stat-row"><span className="pm-stat-k">Unici</span><span className="pm-stat-v">{s.uniq?.toLocaleString() ?? '—'}</span></div>
                  <div className="pm-stat-row"><span className="pm-stat-k">Min</span><span className="pm-stat-v">{s.min ?? '—'}</span></div>
                  <div className="pm-stat-row"><span className="pm-stat-k">Max</span><span className="pm-stat-v">{s.max ?? '—'}</span></div>
                  <div className="pm-stat-row"><span className="pm-stat-k">Null</span><span className={`pm-stat-v ${s.nullCount === 0 ? 'ok' : 'bad'}`}>{s.nullCount}</span></div>
                </>
              )}
            </div>

            <div className="pm-null-bar">
              <div className="pm-null-fill" style={{ width: `${pNull}%`, background: nullColor }} />
            </div>
          </div>
        );
      })}
    </div>
  );
});
StatsTab.displayName = 'StatsTab';
const PreviewModal = memo(({
  isOpen,
  onClose,
  onRefresh,
  data,
  loading = false,
  error = null,
  // PR-cleanup UX Tier 1: quando il task preview ha rilevato permessi
  // revocati mid-task, il consumer passa flag dedicato e PreviewModal
  // renderizza <PermissionRevokedBanner> inline invece dell'errore
  // generico. Default false: backward-compat per chi non passa la prop.
  permissionRevoked = false,
  title = 'Anteprima Dati',
  projectType = 'estrai',
  initialLimit = 100,
  disableGrouping = false,
}) => {
  const [activeTab, setActiveTab] = useState('table');
  const [currentPage, setCurrentPage] = useState(1);
  const [sortColumn, setSortColumn] = useState(null);
  const [sortDirection, setSortDirection] = useState('asc');
  const [searchTerm, setSearchTerm] = useState('');
  const [selectedLimit, setSelectedLimit] = useState(initialLimit);
  const [collapsedGroups, setCollapsedGroups] = useState({});

  const rowsPerPage = 20;
  const limitOptions = [10, 50, 100, 250, 500, 1000];

  const columns = data?.columns || [];
  const rows = data?.rows || [];
  const totalCount = data?.total_count || 0;
  const previewCount = data?.preview_count || rows.length;
  const truncated = data?.truncated || false;

  // Rileva se ci sono colonne geometriche
  const hasGeo = useMemo(() => columns.some(c => isGeoType(c.type)), [columns]);

  // Reset al cambio dati
  useEffect(() => {
    setCurrentPage(1);
    setSortColumn(null);
    setSortDirection('asc');
    setSearchTerm('');
    setActiveTab('table');
    setCollapsedGroups({});
  }, [data]);

  // Filtraggio
  const filteredRows = useMemo(() => {
    if (!searchTerm.trim()) return rows;
    const term = searchTerm.toLowerCase();
    return rows.filter(row =>
      Object.values(row).some(val => String(val).toLowerCase().includes(term))
    );
  }, [rows, searchTerm]);

  // Ordinamento
  const sortedRows = useMemo(() => {
    if (!sortColumn) return filteredRows;
    return [...filteredRows].sort((a, b) => {
      const aVal = a[sortColumn];
      const bVal = b[sortColumn];
      if (aVal == null && bVal == null) return 0;
      if (aVal == null) return sortDirection === 'asc' ? 1 : -1;
      if (bVal == null) return sortDirection === 'asc' ? -1 : 1;
      if (typeof aVal === 'number' && typeof bVal === 'number')
        return sortDirection === 'asc' ? aVal - bVal : bVal - aVal;
      return sortDirection === 'asc'
        ? String(aVal).localeCompare(String(bVal))
        : String(bVal).localeCompare(String(aVal));
    });
  }, [filteredRows, sortColumn, sortDirection]);

  const totalPages = Math.ceil(sortedRows.length / rowsPerPage);
  const handleSort = (colName) => {
    if (sortColumn === colName) {
      setSortDirection(d => d === 'asc' ? 'desc' : 'asc');
    } else {
      setSortColumn(colName);
      setSortDirection('asc');
    }
  };

  const handlePageChange = (page) =>
    setCurrentPage(Math.max(1, Math.min(page, totalPages)));

  // Contract PreviewTable: search resetta currentPage -> 1 (coupling preservato post-split)
  const handleSearchChange = (value) => {
    setSearchTerm(value);
    setCurrentPage(1);
  };

  const handleToggleGroup = (prefix) =>
    setCollapsedGroups(prev => ({ ...prev, [prefix]: !prev[prefix] }));

  // Formattazione cella
  const formatCellValue = (value, type) => {
    if (value == null)
      return <span className="preview-modal__null">NULL</span>;
    // Oggetti/array → JSON string (evita crash React "Objects are not valid as child")
    if (typeof value === 'object') {
      const json = JSON.stringify(value);
      return json.length > 100
        ? <span title={json} className="preview-modal__json">{json.substring(0, 97)}…</span>
        : <span className="preview-modal__json">{json}</span>;
    }
    if (isGeoType(type)) {
      const str = String(value);
      const geomType = extractGeomType(str);
      return <span className="preview-modal__geom-pill" title={str}>{geomType}</span>;
    }
    if (type === 'bool') return value ? '✔' : '✗';
    if (type === 'date' || type === 'datetime')
      return <span className="preview-modal__date">{String(value)}</span>;
    if (type === 'int' || type === 'real')
      return <span className="preview-modal__number">{value}</span>;
    const str = String(value);
    return str.length > 100 ? <span title={str}>{str.substring(0, 97)}…</span> : str;
  };

  const subtitle = !loading && !error
    ? truncated
      ? `${previewCount.toLocaleString()} / ${totalCount.toLocaleString()} righe • ${columns.length} col`
      : `${rows.length.toLocaleString()} righe • ${columns.length} col`
    : null;

  const footer = (
    <>
      {truncated && (
        <span className="preview-modal__truncated-warning">
          ⚠️ Anteprima limitata a {previewCount.toLocaleString()} righe
        </span>
      )}
      <ModalButton variant="secondary" onClick={onClose}>Chiudi</ModalButton>
    </>
  );

  const variant = projectType === 'manipola' ? 'manipola' : projectType === 'arricchisci' ? 'arricchisci' : 'estrai';

  return (
    <ModalWrapper
      isOpen={isOpen}
      onClose={onClose}
      title={title}
      subtitle={subtitle}
      icon="👁️"
      size="xl"
      variant={variant}
      footer={footer}
      loading={loading}
      loadingText="Caricamento anteprima..."
    >
      {/* Permission revoked banner (PR-cleanup UX Tier 1) */}
      {permissionRevoked && !loading && (
        <PermissionRevokedBanner message={error} />
      )}

      {/* Error generico (skip se permission_revoked: il banner sopra
          gia' veicola l'esito specifico) */}
      {error && !loading && !permissionRevoked && (
        <div className="preview-modal__error">
          <div className="preview-modal__error-icon">❌</div>
          <p className="preview-modal__error-title">Errore caricamento anteprima</p>
          <p className="preview-modal__error-message">{error}</p>
        </div>
      )}

      {/* Empty */}
      {!loading && !error && rows.length === 0 && (
        <div className="preview-modal__empty">
          <div className="preview-modal__empty-icon">🔭</div>
          <p>Nessun dato da visualizzare</p>
          <p className="preview-modal__empty-hint">La query non ha restituito risultati</p>
        </div>
      )}

      {/* Content */}
      {!loading && !error && rows.length > 0 && (
        <div className="pm-layout">
<div className="pm-tabs">
            <button
              type="button"
              className={`pm-tab${activeTab === 'table' ? ' active' : ''}`}
              onClick={() => setActiveTab('table')}
            >
              <span>⊞</span> Tabella
            </button>

            {hasGeo && (
              <button
                type="button"
                className={`pm-tab pm-tab--map${activeTab === 'map' ? ' active' : ''}`}
                onClick={() => setActiveTab('map')}
              >
                <span>🗺</span> Mappa
                <span className="pm-geo-badge">GEO</span>
              </button>
            )}

            <button
              type="button"
              className={`pm-tab${activeTab === 'stats' ? ' active' : ''}`}
              onClick={() => setActiveTab('stats')}
            >
              <span>◫</span> Statistiche
            </button>

            <div className="pm-tabs-spacer" />

            <span className="pm-rows-count">
              {sortedRows.length.toLocaleString()} righe
            </span>
          </div>
<div className={`pm-tab-content${activeTab === 'table' ? ' active' : ''}`}>
            <PreviewTable
              columns={columns}
              sortedRows={sortedRows}
              sortColumn={sortColumn}
              sortDirection={sortDirection}
              searchTerm={searchTerm}
              selectedLimit={selectedLimit}
              currentPage={currentPage}
              rowsPerPage={rowsPerPage}
              totalPages={totalPages}
              collapsedGroups={collapsedGroups}
              formatCellValue={formatCellValue}
              disableGrouping={disableGrouping}
              data={data}
              initialLimit={initialLimit}
              limitOptions={limitOptions}
              onSort={handleSort}
              onSearchChange={handleSearchChange}
              onLimitChange={setSelectedLimit}
              onPageChange={handlePageChange}
              onToggleGroup={handleToggleGroup}
              onRefresh={onRefresh}
            />
          </div>
{hasGeo && (
            <div className={`pm-tab-content pm-tab-content--map${activeTab === 'map' ? ' active' : ''}`}>
              <MapTab rows={rows} columns={columns} active={activeTab === 'map'} />
            </div>
          )}
<div className={`pm-tab-content pm-tab-content--stats${activeTab === 'stats' ? ' active' : ''}`}>
            <StatsTab rows={rows} columns={columns} />
          </div>

        </div>
      )}
    </ModalWrapper>
  );
});

PreviewModal.displayName = 'PreviewModal';
export default PreviewModal;
