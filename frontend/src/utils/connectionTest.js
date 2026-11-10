/**
 * connectionTest — il «Test connessione» per categoria, UNA volta per la
 * pagina Connessioni e per il pannello della home per catene (10/11/2026).
 * La scrittura invalida la lista della categoria (utils/sharedInvalidations).
 */
import { testConnection } from '../api';
import { testFeatureConnection } from '../api/features';

/** category: 'database' | 'arcgis' (come `_category` della pagina Connessioni). */
export const testConnectionOf = (category, id) => (category === 'arcgis' ? testFeatureConnection(id) : testConnection(id));
