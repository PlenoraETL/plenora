/**
 * ImdfPreviewModal.jsx — Viewer IMDF con maplibre-gl + deck.gl
 *
 * Design ispirato a dxf_imdf_v2/viewer: tema scuro cyan, sidebar con
 * navigazione livelli, toggle 2D/3D, legenda. Consuma direttamente il
 * task result (`imdfData.geojson` + `imdfData.renderers`) senza chiamate
 * backend: tutto viene renderizzato client-side.
 *
 * Apple IMDF usa LocalizedString (`{it, en, ...}`) per i campi name — la
 * helper `ls()` estrae la stringa prima disponibile.
 */
import React, { useEffect, useMemo, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import maplibregl from 'maplibre-gl';
import 'maplibre-gl/dist/maplibre-gl.css';
import { Deck, GeoJsonLayer, PolygonLayer } from 'deck.gl';
import {
  makeDeckColorFn,
  IMDF_CATEGORY_COLORS,
} from '../../utils/esriToLeafletStyle';

// ── Helpers ─────────────────────────────────────────────────────────────
function ls(v) {
  if (v == null) return '';
  if (typeof v === 'string') return v;
  if (typeof v === 'object') return v.it || v.en || Object.values(v)[0] || '';
  return String(v);
}

function catColor(cat, alpha = 200) {
  const key = String(cat || '').toLowerCase().replace(/[\s_-]/g, '');
  const c = IMDF_CATEGORY_COLORS[key] || IMDF_CATEGORY_COLORS.unspecified;
  return [c[0], c[1], c[2], alpha];
}

// Calcola bounds [minLng, minLat, maxLng, maxLat] da una FeatureCollection
function computeBounds(fc) {
  if (!fc || !fc.features || fc.features.length === 0) return null;
  let minLng = Infinity, minLat = Infinity, maxLng = -Infinity, maxLat = -Infinity;
  const walk = (coords) => {
    if (typeof coords[0] === 'number') {
      const [lng, lat] = coords;
      if (lng < minLng) minLng = lng;
      if (lat < minLat) minLat = lat;
      if (lng > maxLng) maxLng = lng;
      if (lat > maxLat) maxLat = lat;
    } else {
      coords.forEach(walk);
    }
  };
  fc.features.forEach(f => { if (f.geometry?.coordinates) walk(f.geometry.coordinates); });
  if (!isFinite(minLng)) return null;
  return [minLng, minLat, maxLng, maxLat];
}

function boundsCenter(b) {
  return [(b[0] + b[2]) / 2, (b[1] + b[3]) / 2];
}

// Zoom rough da bounds (degrees)
function boundsZoom(b) {
  const dLng = Math.abs(b[2] - b[0]);
  const dLat = Math.abs(b[3] - b[1]);
  const d = Math.max(dLng, dLat);
  if (d === 0) return 19;
  // Zoom ~ log2(360 / d) - 1 (cap a 19 = max OSM)
  return Math.max(10, Math.min(19, Math.log2(360 / d) - 1));
}

// ── Componente ──────────────────────────────────────────────────────────
export default function ImdfPreviewModal({ isOpen, onClose, imdfData }) {
  const mapRef       = useRef(null);  // maplibregl.Map
  const deckRef      = useRef(null);  // deck.Deck
  const basemapRef   = useRef(null);  // DOM div per maplibre
  const canvasRef    = useRef(null);  // canvas per deck.gl

  const [selectedLevel, setSelectedLevel]   = useState('all');
  const [colorMode,     setColorMode]       = useState('category'); // 'renderer' | 'category'
  const [is3d,          setIs3d]            = useState(false);
  const [showOpenings,  setShowOpenings]    = useState(false);
  const [selectedUnit,  setSelectedUnit]    = useState(null);
  const [deckReady,     setDeckReady]       = useState(false);
  const [viewState,     setViewState]       = useState({
    longitude: 12.65, latitude: 45.95, zoom: 18, pitch: 0, bearing: 0,
  });

  const geojson    = imdfData?.geojson   || {};
  const renderers  = imdfData?.renderers || null;
  const siteId     = imdfData?.site_id;

  // ── Albero navigazione: venue → building → level ──
  const navTree = useMemo(() => {
    const venueFeatures    = geojson.venue?.features    || [];
    const buildingFeatures = geojson.building?.features || [];
    const levelFeatures    = geojson.level?.features    || [];

    // Map level by building_ids
    const byBuilding = {};
    levelFeatures.forEach(lf => {
      const p = lf.properties || {};
      const bids = Array.isArray(p.building_ids) ? p.building_ids : (p.building_id ? [p.building_id] : []);
      const lid  = p.level_id || lf.id;
      const entry = {
        level_id: lid,
        name:     ls(p.short_name) || ls(p.name) || lid,
        ordinal:  p.ordinal ?? 0,
      };
      if (bids.length === 0) {
        byBuilding['_orphan'] = byBuilding['_orphan'] || [];
        byBuilding['_orphan'].push(entry);
      } else {
        bids.forEach(bid => {
          byBuilding[bid] = byBuilding[bid] || [];
          byBuilding[bid].push(entry);
        });
      }
    });
    Object.values(byBuilding).forEach(arr => arr.sort((a, b) => (b.ordinal || 0) - (a.ordinal || 0)));

    const buildings = buildingFeatures.map(bf => {
      const p = bf.properties || {};
      const bid = p.building_id || bf.id;
      return {
        building_id: bid,
        name:        ls(p.name) || ls(p.alt_name) || bid,
        levels:      byBuilding[bid] || [],
      };
    });

    // Orphan levels (no building match)
    if (byBuilding._orphan && byBuilding._orphan.length > 0) {
      buildings.push({ building_id: '_orphan', name: 'Altri livelli', levels: byBuilding._orphan });
    }

    const venueName = venueFeatures.length > 0
      ? (ls(venueFeatures[0].properties?.name) || siteId || 'Sito')
      : (siteId || 'Sito');

    return { venueName, buildings };
  }, [geojson, siteId]);

  // ── Lista piatta livelli (per selector + 3D stacking) ──
  const allLevels = useMemo(() => {
    const list = [];
    navTree.buildings.forEach(b => b.levels.forEach(l => list.push(l)));
    return list;
  }, [navTree]);

  // ── Stats ──
  const stats = useMemo(() => {
    const u = geojson.unit?.features || [];
    const o = geojson.opening?.features || [];
    const filtered = selectedLevel === 'all' ? u : u.filter(f => f.properties?.level_id === selectedLevel);
    return {
      levels:   allLevels.length,
      units:    filtered.length,
      openings: o.length,
    };
  }, [geojson, selectedLevel, allLevels]);

  // ── Color function per unit (dipende da renderers/colorMode) ──
  const unitColorFn = useMemo(() => {
    if (colorMode === 'renderer' && renderers?.Locale) {
      return makeDeckColorFn(renderers.Locale, { alpha: is3d ? 220 : 200 });
    }
    return (feat) => catColor(feat?.properties?.category, is3d ? 220 : 200);
  }, [colorMode, renderers, is3d]);

  // ── Legenda (da renderer o category) ──
  const legend = useMemo(() => {
    if (colorMode === 'renderer' && renderers?.Locale) {
      const rend = renderers.Locale.renderer || renderers.Locale;
      if (rend.type === 'uniqueValue') {
        return (rend.uniqueValueInfos || []).slice(0, 12).map(uvi => {
          const c = uvi.symbol?.color || [120, 120, 120];
          return { label: String(uvi.value), rgb: `rgb(${c[0]},${c[1]},${c[2]})` };
        });
      }
      if (rend.type === 'simple') {
        const c = rend.symbol?.color || [120, 120, 120];
        return [{ label: 'Tutti', rgb: `rgb(${c[0]},${c[1]},${c[2]})` }];
      }
    }
    // Category mode: estrai categorie presenti
    const units = geojson.unit?.features || [];
    const cats = new Set();
    units.forEach(f => { if (f.properties?.category) cats.add(f.properties.category); });
    return Array.from(cats).slice(0, 12).map(cat => {
      const [r, g, b] = catColor(cat, 255);
      return { label: cat, rgb: `rgb(${r},${g},${b})` };
    });
  }, [colorMode, renderers, geojson]);

  // ── Inizializzazione mappa (sincrona, una sola volta per apertura) ──
  useEffect(() => {
    if (!isOpen || !basemapRef.current || !canvasRef.current) return;
    if (mapRef.current) return; // già inizializzata

    const center = [12.65, 45.95];
    const zoom   = 18;

    const map = new maplibregl.Map({
      container: basemapRef.current,
      style: {
        version: 8,
        sources: {
          osm: {
            type: 'raster',
            tiles: ['https://tile.openstreetmap.org/{z}/{x}/{y}.png'],
            tileSize: 256,
            maxzoom: 19,
            attribution: '© OpenStreetMap',
          },
        },
        layers: [{ id: 'osm', type: 'raster', source: 'osm', paint: { 'raster-opacity': 0.28 } }],
      },
      center, zoom,
      interactive: false,
    });
    mapRef.current = map;

    const deck = new Deck({
      canvas: canvasRef.current,
      width:  '100%',
      height: '100%',
      viewState: { longitude: center[0], latitude: center[1], zoom, pitch: 0, bearing: 0 },
      controller: true,
      onViewStateChange: ({ viewState: vs }) => {
        setViewState(vs);
        map.jumpTo({
          center:  [vs.longitude, vs.latitude],
          zoom:    vs.zoom,
          bearing: vs.bearing,
          pitch:   vs.pitch,
        });
      },
      layers: [],
      getCursor: ({ isDragging }) => (isDragging ? 'grabbing' : 'crosshair'),
    });
    deckRef.current = deck;
    setDeckReady(true);

    // Resize quando il modal cambia dimensioni
    const handleResize = () => {
      setTimeout(() => {
        if (mapRef.current) mapRef.current.resize();
      }, 50);
    };
    window.addEventListener('resize', handleResize);
    handleResize();

    return () => {
      window.removeEventListener('resize', handleResize);
    };
  }, [isOpen]);

  // ── Ricentra la view quando arrivano i dati ──
  const fittedRef = useRef(false);
  useEffect(() => {
    if (!deckReady || !deckRef.current) return;
    if (fittedRef.current) return;
    const bounds = computeBounds(geojson.unit) || computeBounds(geojson.building) || computeBounds(geojson.venue);
    if (!bounds) return;
    const [cx, cy] = boundsCenter(bounds);
    const z = boundsZoom(bounds);
    const vs = { longitude: cx, latitude: cy, zoom: z, pitch: 0, bearing: 0 };
    setViewState(vs);
    if (mapRef.current) mapRef.current.jumpTo({ center: [cx, cy], zoom: z });
    fittedRef.current = true;
  }, [deckReady, geojson]);

  // ── Propaga viewState a deck (controlled) ──
  useEffect(() => {
    if (!deckReady || !deckRef.current) return;
    deckRef.current.setProps({ viewState });
  }, [deckReady, viewState]);

  // ── Aggiorna layers quando cambiano dati/filtri ──
  useEffect(() => {
    if (!deckRef.current) return;
    const deck = deckRef.current;

    const unitFc = geojson.unit;
    if (!unitFc || !unitFc.features) {
      deck.setProps({ layers: [] });
      return;
    }

    // Filtra per livello selezionato
    const filteredFeats = selectedLevel === 'all'
      ? unitFc.features
      : unitFc.features.filter(f => f.properties?.level_id === selectedLevel);
    const filteredFc = { type: 'FeatureCollection', features: filteredFeats };

    // Mappa ordinal per level_id (per stacking 3D)
    const ordByLevel = {};
    allLevels.forEach(l => { ordByLevel[l.level_id] = l.ordinal || 0; });
    const floorHeight = 3.5;

    const onClickCb = ({ object }) => {
      if (!object) { setSelectedUnit(null); return; }
      setSelectedUnit(object.properties || null);
    };

    let unitLayer;
    if (!is3d) {
      unitLayer = new GeoJsonLayer({
        id: 'imdf-units',
        data: filteredFc,
        pickable: true,
        stroked: true,
        filled:  true,
        getFillColor: (f) => unitColorFn(f),
        getLineColor: [255, 255, 255, 55],
        getLineWidth: 0.15,
        lineWidthUnits: 'meters',
        updateTriggers: {
          getFillColor: [colorMode, selectedLevel, is3d],
        },
        onClick: onClickCb,
      });
    } else {
      // 3D: converte in record flat per PolygonLayer con baseZ da ordinal
      const data3d = [];
      filteredFeats.forEach(f => {
        const geom = f.geometry;
        if (!geom) return;
        const ord  = ordByLevel[f.properties?.level_id] ?? 0;
        const baseZ = ord * floorHeight;
        const height = floorHeight - 0.2;
        const pushPoly = (rings) => {
          data3d.push({
            polygon: rings[0].map(([lng, lat]) => [lng, lat, baseZ]),
            properties: f.properties,
            baseZ, height,
          });
        };
        if (geom.type === 'Polygon')        pushPoly(geom.coordinates);
        else if (geom.type === 'MultiPolygon') geom.coordinates.forEach(pushPoly);
      });

      unitLayer = new PolygonLayer({
        id: 'imdf-units-3d',
        data: data3d,
        pickable: true,
        stroked: true,
        filled:  true,
        extruded: true,
        wireframe: false,
        getPolygon:  (d) => d.polygon,
        getElevation:(d) => d.height,
        getFillColor:(d) => unitColorFn({ properties: d.properties }),
        getLineColor:[255, 255, 255, 35],
        getLineWidth: 0.15,
        lineWidthUnits: 'meters',
        material: { ambient: 0.4, diffuse: 0.6, shininess: 32, specularColor: [30, 30, 30] },
        updateTriggers: {
          getFillColor: [colorMode],
        },
        onClick: onClickCb,
      });
    }

    const layers = [unitLayer];

    // Aperture (porte)
    if (showOpenings && geojson.opening?.features) {
      const openFeats = selectedLevel === 'all'
        ? geojson.opening.features
        : geojson.opening.features.filter(f => f.properties?.level_id === selectedLevel);
      if (openFeats.length > 0) {
        layers.push(new GeoJsonLayer({
          id: 'imdf-openings',
          data: { type: 'FeatureCollection', features: openFeats },
          pickable: false, stroked: true, filled: false,
          getLineColor: [255, 120, 50, 230],
          getLineWidth: 0.35,
          lineWidthUnits: 'meters',
        }));
      }
    }

    deck.setProps({ layers });
  }, [deckReady, geojson, selectedLevel, colorMode, is3d, showOpenings, unitColorFn, allLevels]);

  // ── Pitch/bearing transition quando switch 2D/3D ──
  useEffect(() => {
    if (!deckRef.current) return;
    const deck = deckRef.current;
    const vm = deck.viewManager;
    const vs = vm?.getViewports?.()[0]?.viewState;
    if (!vs) return;
    deck.setProps({
      initialViewState: {
        longitude: vs.longitude, latitude: vs.latitude, zoom: vs.zoom,
        pitch:   is3d ? 45  : 0,
        bearing: is3d ? -20 : 0,
        transitionDuration: 600,
      },
    });
  }, [is3d]);

  // ── Cleanup alla chiusura ──
  useEffect(() => {
    if (isOpen) return;
    if (deckRef.current) { try { deckRef.current.finalize(); } catch (_) {} deckRef.current = null; }
    if (mapRef.current)  { try { mapRef.current.remove();    } catch (_) {} mapRef.current  = null; }
    setSelectedUnit(null);
    setDeckReady(false);
    fittedRef.current = false;
  }, [isOpen]);

  if (!isOpen) return null;

  const hasData = imdfData && geojson.unit && geojson.unit.features && geojson.unit.features.length > 0;

  return createPortal((
    <div
      onClick={onClose}
      style={{
        position: 'fixed', inset: 0,
        background: 'rgba(0,0,0,0.72)',
        display: 'flex', alignItems: 'center', justifyContent: 'center',
        zIndex: 'var(--z-modal-priority, 10000)',
        fontFamily: "'JetBrains Mono', ui-monospace, Menlo, monospace",
      }}
    >
      <div
        onClick={(e) => e.stopPropagation()}
        style={{
          background: 'var(--plen-surface, #0C0F18)',
          color: 'var(--plen-text, #DDE4F2)',
          border: '1px solid var(--plen-border, rgba(255,255,255,0.08))',
          borderRadius: 10,
          width: 'min(1400px, 97vw)',
          height: 'min(880px, 94vh)',
          boxShadow: '0 24px 80px rgba(0,0,0,0.7)',
          display: 'flex',
          overflow: 'hidden',
        }}
      >
        {/* ── SIDEBAR ── */}
        <aside style={{
          width: 300, flexShrink: 0,
          background: 'var(--plen-surface-2, #111525)',
          borderRight: '1px solid var(--plen-border, rgba(255,255,255,0.08))',
          display: 'flex', flexDirection: 'column',
          overflow: 'hidden',
        }}>
          {/* Header */}
          <div style={{ padding: '16px 18px 12px', borderBottom: '1px solid var(--plen-border, rgba(255,255,255,0.08))' }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
              <h1 style={{ margin: 0, fontSize: 16, fontWeight: 800, color: '#7c3aed', letterSpacing: 0.3 }}>
                IMDF Viewer
              </h1>
              <button type="button" onClick={onClose} style={{
                background: 'none', border: 'none', color: 'var(--plen-text-sub, #7D8BA8)', fontSize: 18, cursor: 'pointer', padding: 2,
              }}>✕</button>
            </div>
            <div style={{ fontSize: 9, color: 'var(--plen-text-sub, #7D8BA8)', textTransform: 'uppercase', letterSpacing: 0.5, marginTop: 3 }}>
              {siteId ? `Site · ${siteId}` : 'Indoor Mapping Preview'}
            </div>
          </div>

          {/* Stats */}
          <div style={{ display: 'flex', borderBottom: '1px solid var(--plen-border, rgba(255,255,255,0.08))' }}>
            <Stat label="Livelli" value={stats.levels} />
            <Stat label="Unità"   value={stats.units} />
            <Stat label="Porte"   value={stats.openings} last />
          </div>

          {/* Sezione nav */}
          <div style={{
            padding: '10px 14px 6px', fontSize: 9, color: 'var(--plen-text-sub, #7D8BA8)',
            textTransform: 'uppercase', letterSpacing: 1, borderBottom: '1px solid var(--plen-border, rgba(255,255,255,0.08))',
          }}>
            Navigazione
          </div>
          <div style={{ flex: 1, overflowY: 'auto', padding: 6 }}>
            <NavItem
              label={`📍 ${navTree.venueName}`}
              active={selectedLevel === 'all'}
              onClick={() => setSelectedLevel('all')}
              badge={stats.levels}
              level={0}
            />
            {navTree.buildings.map(b => (
              <div key={b.building_id}>
                <div style={{
                  fontSize: 10, color: 'var(--plen-manipola, #7c3aed)', fontWeight: 600,
                  padding: '6px 8px 2px 14px', textTransform: 'uppercase', letterSpacing: 0.3,
                }}>
                  🏢 {b.name}
                </div>
                {b.levels.map(l => (
                  <NavItem
                    key={l.level_id}
                    label={`▫ ${l.name}`}
                    active={selectedLevel === l.level_id}
                    onClick={() => setSelectedLevel(l.level_id)}
                    level={2}
                  />
                ))}
              </div>
            ))}
          </div>

          {/* Color mode */}
          <div style={{
            display: 'flex', alignItems: 'center', gap: 6,
            padding: '8px 14px', borderTop: '1px solid var(--plen-border, rgba(255,255,255,0.08))',
          }}>
            <label style={{ fontSize: 9, color: 'var(--plen-text-sub, #7D8BA8)', textTransform: 'uppercase' }}>Colora</label>
            <select
              value={colorMode}
              onChange={e => setColorMode(e.target.value)}
              style={{
                flex: 1, background: 'var(--plen-surface-2, #111525)', border: '1px solid var(--plen-border, rgba(255,255,255,0.08))',
                color: 'var(--plen-text, #DDE4F2)', fontFamily: 'inherit', fontSize: 10,
                padding: '4px 8px', borderRadius: 4, outline: 'none',
              }}
            >
              <option value="renderer">Stili renderer</option>
              <option value="category">Category IMDF</option>
            </select>
          </div>

          {/* Toggle 2D/3D + porte */}
          <div style={{ display: 'flex', gap: 6, padding: '8px 14px', borderTop: '1px solid var(--plen-border, rgba(255,255,255,0.08))' }}>
            <TogBtn on={!is3d}        onClick={() => setIs3d(false)} label="▦ 2D" />
            <TogBtn on={is3d}         onClick={() => setIs3d(true)}  label="◈ 3D" />
            <TogBtn on={showOpenings} onClick={() => setShowOpenings(v => !v)} label="◫ Porte" />
          </div>

          {/* Legenda */}
          <div style={{
            padding: '8px 14px', borderTop: '1px solid var(--plen-border, rgba(255,255,255,0.08))',
            maxHeight: 180, overflowY: 'auto',
          }}>
            <div style={{ fontSize: 9, color: 'var(--plen-text-sub, #7D8BA8)', textTransform: 'uppercase', letterSpacing: 1, marginBottom: 6 }}>
              Legenda
            </div>
            {legend.length === 0 && <div style={{ fontSize: 9, color: 'var(--plen-text-sub, #7D8BA8)' }}>—</div>}
            {legend.map((l, i) => (
              <div key={i} style={{ display: 'flex', alignItems: 'center', gap: 7, marginBottom: 4 }}>
                <div style={{ width: 11, height: 11, borderRadius: 2, background: l.rgb, flexShrink: 0 }} />
                <div style={{ fontSize: 9, color: 'var(--plen-text, #DDE4F2)' }}>{l.label}</div>
              </div>
            ))}
          </div>
        </aside>

        {/* ── AREA MAPPA ── */}
        <div style={{ flex: 1, position: 'relative', overflow: 'hidden', background: 'var(--plen-surface, #0C0F18)' }}>
          {hasData ? (
            <>
              <div ref={basemapRef} style={{ position: 'absolute', inset: 0 }} />
              <canvas ref={canvasRef} style={{ position: 'absolute', inset: 0, width: '100%', height: '100%' }} />

              {/* Pannello dettaglio unit selezionata */}
              {selectedUnit && (
                <div style={{
                  position: 'absolute', top: 14, right: 14,
                  width: 280,
                  background: 'var(--plen-surface-2, #111525)',
                  border: '1px solid var(--plen-border, rgba(255,255,255,0.08))',
                  borderRadius: 8,
                  padding: '12px 14px',
                  boxShadow: '0 8px 32px rgba(0,0,0,0.55)',
                  zIndex: 10,
                }}>
                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start' }}>
                    <div style={{ flex: 1, minWidth: 0 }}>
                      <div style={{ fontSize: 12, fontWeight: 700, color: '#7c3aed', wordBreak: 'break-all' }}>
                        {ls(selectedUnit.name) || selectedUnit.unit_id || '—'}
                      </div>
                      <div style={{ fontSize: 9, color: 'var(--plen-text-sub, #7D8BA8)', marginTop: 2 }}>
                        {selectedUnit.unit_id || ''}
                      </div>
                    </div>
                    <button type="button" onClick={() => setSelectedUnit(null)} style={{
                      background: 'none', border: 'none', color: 'var(--plen-text-sub, #7D8BA8)', fontSize: 16, cursor: 'pointer', padding: 0,
                    }}>✕</button>
                  </div>
                  <div style={{ marginTop: 10, display: 'flex', flexDirection: 'column', gap: 4 }}>
                    <DetailRow k="Category" v={selectedUnit.category} />
                    <DetailRow k="Level"    v={selectedUnit.level_id} />
                    <DetailRow k="Restr."   v={selectedUnit.restriction} />
                    <DetailRow k="Alt"      v={ls(selectedUnit.alt_name)} />
                    {selectedUnit.extension && typeof selectedUnit.extension === 'object' && (
                      <div style={{ marginTop: 6, paddingTop: 6, borderTop: '1px solid var(--plen-border, rgba(255,255,255,0.08))' }}>
                        <div style={{ fontSize: 8, color: 'var(--plen-text-sub, #7D8BA8)', textTransform: 'uppercase', letterSpacing: 1, marginBottom: 4 }}>
                          Extension
                        </div>
                        {Object.entries(selectedUnit.extension).slice(0, 10).map(([k, v]) => (
                          <DetailRow key={k} k={k} v={v} />
                        ))}
                      </div>
                    )}
                  </div>
                </div>
              )}
            </>
          ) : (
            <div style={{
              position: 'absolute', inset: 0, display: 'flex',
              alignItems: 'center', justifyContent: 'center',
              color: 'var(--plen-text-sub, #7D8BA8)', fontSize: 13,
            }}>
              Nessun dato IMDF disponibile.
            </div>
          )}
        </div>
      </div>
    </div>
  ), document.body);
}

// ── Sub-components ──────────────────────────────────────────────────────
function Stat({ label, value, last }) {
  return (
    <div style={{
      flex: 1, padding: '10px 12px', textAlign: 'center',
      borderRight: last ? 'none' : '1px solid var(--plen-border, rgba(255,255,255,0.08))',
    }}>
      <div style={{ fontSize: 18, fontWeight: 600, color: '#7c3aed' }}>{value ?? '—'}</div>
      <div style={{ fontSize: 9, color: 'var(--plen-text-sub, #7D8BA8)', textTransform: 'uppercase', letterSpacing: 0.5 }}>{label}</div>
    </div>
  );
}

function NavItem({ label, active, onClick, badge, level = 0 }) {
  const pad = 8 + level * 10;
  return (
    <button
      type="button"
      onClick={onClick}
      style={{
        display: 'flex', alignItems: 'center', justifyContent: 'space-between',
        width: '100%', padding: `6px 10px 6px ${pad}px`, margin: '2px 0',
        background: active ? 'rgba(124,58,237,0.12)' : 'transparent',
        border: '1px solid',
        borderColor: active ? '#7c3aed' : 'transparent',
        borderRadius: 5,
        color: active ? '#7c3aed' : 'var(--plen-text, #DDE4F2)',
        fontFamily: 'inherit', fontSize: 11,
        cursor: 'pointer', textAlign: 'left',
      }}
    >
      <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{label}</span>
      {badge != null && (
        <span style={{
          fontSize: 9, padding: '1px 6px',
          background: active ? 'rgba(124,58,237,0.2)' : 'var(--plen-surface-2, #111525)',
          borderRadius: 8,
          color: active ? '#7c3aed' : 'var(--plen-text-sub, #7D8BA8)',
        }}>{badge}</span>
      )}
    </button>
  );
}

function TogBtn({ on, onClick, label }) {
  return (
    <button
      type="button"
      onClick={onClick}
      style={{
        flex: 1, padding: '5px 8px', borderRadius: 4,
        fontFamily: 'inherit', fontSize: 9, fontWeight: 600,
        cursor: 'pointer',
        border: '1px solid',
        borderColor: on ? '#7c3aed' : 'var(--plen-surface-2, #111525)',
        background:  on ? 'rgba(124,58,237,0.15)' : 'var(--plen-surface-2, #111525)',
        color: on ? '#7c3aed' : 'var(--plen-text-sub, #7D8BA8)',
        textTransform: 'uppercase', letterSpacing: 0.5,
      }}
    >{label}</button>
  );
}

function DetailRow({ k, v }) {
  if (v == null || v === '') return null;
  return (
    <div style={{ display: 'flex', gap: 8, fontSize: 10 }}>
      <div style={{ color: 'var(--plen-text-sub, #7D8BA8)', minWidth: 70, flexShrink: 0 }}>{k}</div>
      <div style={{ color: 'var(--plen-text, #DDE4F2)', flex: 1, wordBreak: 'break-word' }}>
        {typeof v === 'object' ? JSON.stringify(v) : String(v)}
      </div>
    </div>
  );
}
