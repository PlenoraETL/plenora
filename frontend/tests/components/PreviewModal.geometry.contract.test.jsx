/**
 * PreviewModal con una colonna geometrica (Fase 5, P7): tabella, statistiche
 * e mappa leggono le geometrie dal lettore unico (utils/geometry). Un valore
 * non leggibile compare come «non leggibile» e non entra nella mappa, che si
 * disegna con le altre righe.
 */
import React from 'react';
import { render, screen, within, act } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, it, expect, vi } from 'vitest';

const leaflet = vi.hoisted(() => {
  const layer = { getBounds: () => ({ isValid: () => false }), addTo() { return this; }, setStyle() {} };
  const map = {
    setView() { return this; }, invalidateSize() {}, fitBounds() {}, remove() {}, removeLayer() {},
  };
  return {
    geoJSON: vi.fn(() => layer),
    map: vi.fn(() => map),
    tileLayer: vi.fn(() => ({ addTo() { return this; } })),
    circleMarker: vi.fn(),
  };
});
vi.mock('leaflet', () => ({ default: leaflet }));
vi.mock('leaflet/dist/leaflet.css', () => ({}));

import PreviewModal from '../../src/components/modals/PreviewModal';

const rows = [
  { id: 1, geom: 'SRID=4326;MULTIPOINT (10 40, 40 30)' },
  { id: 2, geom: '01010000C0000000000000F03F000000000000004000000000000008400000000000001040' },
  { id: 3, geom: 'POINT (1)' },
  { id: 4, geom: 'POINT EMPTY' },
  { id: 5, geom: { type: 'LineString', coordinates: [[0, 0, 9], [1, 1, 9]] } },
];

const renderModal = () => render(
  <PreviewModal
    isOpen
    onClose={vi.fn()}
    data={{
      columns: [{ name: 'id', type: 'int' }, { name: 'geom', type: 'geometry' }],
      rows, total_count: rows.length, preview_count: rows.length, truncated: false,
    }}
    loading={false}
    error={null}
    title="Geo"
    projectType="estrai"
    initialLimit={100}
  />,
);

describe('PreviewModal — geometrie', () => {
  it('la tabella mostra il tipo letto, anche da WKB e GeoJSON, e «non leggibile»', () => {
    renderModal();
    const table = screen.getByRole('table');
    expect(within(table).getByText('MULTIPOINT')).toBeInTheDocument();
    expect(within(table).getAllByText('POINT')).toHaveLength(2);
    expect(within(table).getByText('LINESTRING')).toBeInTheDocument();
    expect(within(table).getByText('non leggibile')).toBeInTheDocument();
  });

  it('la mappa disegna le geometrie leggibili non vuote e conta le altre', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    renderModal();
    await userEvent.click(screen.getByRole('button', { name: /mappa/i }));
    await act(async () => { vi.advanceTimersByTime(500); });
    vi.useRealTimers();

    expect(leaflet.geoJSON).toHaveBeenCalledTimes(1);
    const features = leaflet.geoJSON.mock.calls[0][0].features;
    expect(features.map(f => f.properties.id)).toEqual([1, 2, 5]);
    expect(features.map(f => f.geometry)).toEqual([
      { type: 'MultiPoint', coordinates: [[10, 40], [40, 30]] },
      { type: 'Point', coordinates: [1, 2] },
      { type: 'LineString', coordinates: [[0, 0], [1, 1]] },
    ]);
    expect(screen.getByText(/3 elementi · MultiPoint/)).toHaveTextContent('non leggibile: 1');
  });

  it('statistiche: tipi dichiarati dal lettore unico (geometryType), Esri JSON compreso', async () => {
    renderModal();
    await userEvent.click(screen.getByRole('button', { name: /statistiche/i }));
    // 'POINT (1)' ha un tipo dichiarato valido: conta come POINT (la tabella lo dice «non leggibile»).
    expect(screen.getByText('MULTIPOINT, POINT, LINESTRING')).toBeInTheDocument();
  });

  it('Esri JSON (ArcGIS) letto come geometria: tabella e mappa', async () => {
    const esri = [
      { id: 1, geom: { x: 11.34, y: 44.49, spatialReference: { wkid: 4326 } } },
      { id: 2, geom: { paths: [[[0, 0], [1, 1]]], spatialReference: { wkid: 4326 } } },
      { id: 3, geom: '{"rings":[[[0,0],[0,1],[1,1],[0,0]]]}' },
    ];
    render(
      <PreviewModal isOpen onClose={vi.fn()} loading={false} error={null} title="Esri" projectType="estrai" initialLimit={100}
        data={{ columns: [{ name: 'id', type: 'int' }, { name: 'geom', type: 'geometry' }], rows: esri,
          total_count: esri.length, preview_count: esri.length, truncated: false }} />,
    );
    const table = screen.getByRole('table');
    expect(['POINT', 'LINESTRING', 'POLYGON'].map(t => within(table).getByText(t))).toHaveLength(3);
    leaflet.geoJSON.mockClear();
    vi.useFakeTimers({ shouldAdvanceTime: true });
    await userEvent.click(screen.getByRole('button', { name: /mappa/i }));
    await act(async () => { vi.advanceTimersByTime(500); });
    vi.useRealTimers();
    expect(leaflet.geoJSON.mock.calls[0][0].features.map(f => f.geometry.type)).toEqual(['Point', 'LineString', 'Polygon']);
  });
});
