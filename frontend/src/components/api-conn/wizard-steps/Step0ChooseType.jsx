/**
 * Step0ChooseType.jsx — Step 0 del wizard ApiConnection
 *
 * Scelta tipo API (enricher / generator), categoria, tipo connessione
 * (manual / template / openapi / rest_file / datasource).
 *
 * Estratto in `FE-PHASE2-SPLIT-001B`. Presentational: riceve formData
 * e callback di update, nessun state interno, nessuna fetch, nessuna
 * chiamata API.
 */
import React from 'react';

const CATEGORIES_NEW = [
  { id:'geocoding',  label:'Geocoding',   icon:'🌍' },
  { id:'routing',    label:'Routing',     icon:'🚗' },
  { id:'catasto',    label:'Catasto',     icon:'🏠' },
  { id:'datasource', label:'Data Source', icon:'📁' },
  { id:'custom',     label:'Custom',      icon:'⚙️' },
];

const CONNECTION_TYPES = [
  { id:'template',   label:'Da Template',     icon:'🍪', desc:'Template pre-configurato' },
  { id:'manual',     label:'Manuale',         icon:'✏️', desc:'URL e parametri manuali' },
  { id:'openapi',    label:'OpenAPI',         icon:'📋', desc:'Schema OpenAPI/Swagger' },
  { id:'rest_file',  label:'Processore File', icon:'📦', desc:'API REST con file in input e output' },
  { id:'datasource', label:'Data Source',     icon:'📁', desc:'SharePoint, FTP, S3…' },
];

export default function Step0ChooseType({ formData, setFormData }) {
  return (
    <div className="acm__wstep-content">
      <div className="acm__wlabel">Tipo API</div>
      <div className="acm__type-cards">
        {[
          {v:'enricher',icon:'🔄',label:'Arricchitore',desc:'Chiamata per ogni riga del dataset',ex:'Geocoding, lookup, validazione'},
          {v:'generator',icon:'📥',label:'Generatore',desc:'Chiamata singola, genera nuovo dataset',ex:'POI, meteo, cataloghi'},
        ].map(t=>(
          <div key={t.v} className={`acm__type-card${formData.api_type===t.v?' acm__type-card--on':''}`} onClick={()=>setFormData({...formData,api_type:t.v})}>
            <div className="acm__type-card-icon">{t.icon}</div>
            <div className="acm__type-card-label">{t.label}</div>
            <div className="acm__type-card-desc">{t.desc}</div>
            <div className="acm__type-card-ex">{t.ex}</div>
          </div>
        ))}
      </div>
      <div className="acm__wlabel acm__wlabel--mt-md">Categoria</div>
      <div className="acm__pill-group">
        {CATEGORIES_NEW.map(c=>(
          <button type="button" key={c.id}
            className={`acm__pill${formData.category===c.id?' acm__pill--on':''}`}
            onClick={()=>setFormData({...formData,category:c.id})}>
            {c.icon} {c.label}
          </button>
        ))}
      </div>
      <div className="acm__wlabel acm__wlabel--mt-md">Tipo connessione</div>
      <div className="acm__conn-type-list">
        {CONNECTION_TYPES.map(ct=>(
          <div key={ct.id} className={`acm__conn-type${formData.connection_type===ct.id?' acm__conn-type--on':''}`} onClick={()=>setFormData({...formData,connection_type:ct.id})}>
            <span className="acm__conn-type-icon">{ct.icon}</span>
            <div><div className="acm__conn-type-label">{ct.label}</div><div className="acm__conn-type-desc">{ct.desc}</div></div>
          </div>
        ))}
      </div>
    </div>
  );
}
