/**
 * Template Variables Processor
 * 
 * Processa variabili template nei valori statici.
 * Usato dal nodo StaticValuesNode e dal backend per risolvere
 * variabili dinamiche come {{NOW}}, {{TODAY}}, ecc.
 * 
 * Sintassi supportata:
 * - {{NOW}}           → Data/ora corrente (YYYY-MM-DD HH:mm)
 * - {{NOW+2h}}        → Ora corrente + 2 ore
 * - {{NOW-30m}}       → Ora corrente - 30 minuti
 * - {{NOW-1d}}        → Ieri stessa ora
 * - {{TODAY}}         → Oggi alle 00:00
 * - {{YESTERDAY}}     → Ieri alle 00:00
 * - {{TOMORROW}}      → Domani alle 00:00
 * - {{DATE}}          → Solo data (YYYY-MM-DD)
 * - {{TIME}}          → Solo ora (HH:mm)
 * - {{TIMESTAMP}}     → Unix timestamp in millisecondi
 * - {{YEAR}}          → Anno corrente
 * - {{MONTH}}         → Mese corrente (01-12)
 * - {{DAY}}           → Giorno corrente (01-31)
 */

// Regex per trovare variabili template
const TEMPLATE_REGEX = /\{\{([A-Z]+)([+-]\d+[hdmsMHDMS])?\}\}/g;

// Formatta data in YYYY-MM-DD HH:mm
const formatDateTime = (date) => {
  const year = date.getFullYear();
  const month = String(date.getMonth() + 1).padStart(2, '0');
  const day = String(date.getDate()).padStart(2, '0');
  const hours = String(date.getHours()).padStart(2, '0');
  const minutes = String(date.getMinutes()).padStart(2, '0');
  return `${year}-${month}-${day} ${hours}:${minutes}`;
};

// Formatta solo data YYYY-MM-DD
const formatDate = (date) => {
  const year = date.getFullYear();
  const month = String(date.getMonth() + 1).padStart(2, '0');
  const day = String(date.getDate()).padStart(2, '0');
  return `${year}-${month}-${day}`;
};

// Formatta solo ora HH:mm
const formatTime = (date) => {
  const hours = String(date.getHours()).padStart(2, '0');
  const minutes = String(date.getMinutes()).padStart(2, '0');
  return `${hours}:${minutes}`;
};

// Applica offset a una data
const applyOffset = (date, offsetStr) => {
  if (!offsetStr) return date;
  
  const match = offsetStr.match(/([+-])(\d+)([hdmsMHDMS])/);
  if (!match) return date;
  
  const [, sign, amount, unit] = match;
  const multiplier = sign === '+' ? 1 : -1;
  const value = Number(amount) * multiplier; // amount: solo cifre (regex)
  
  const result = new Date(date);
  
  switch (unit.toLowerCase()) {
    case 's': // secondi
      result.setSeconds(result.getSeconds() + value);
      break;
    case 'm': // minuti
      result.setMinutes(result.getMinutes() + value);
      break;
    case 'h': // ore
      result.setHours(result.getHours() + value);
      break;
    case 'd': // giorni
      result.setDate(result.getDate() + value);
      break;
    default:
      break;
  }
  
  return result;
};

// Ottiene data base per variabile, rispettando referenceDate se fornita
const getBaseDate = (varName, referenceDate = null) => {
  const now = referenceDate ? new Date(referenceDate) : new Date();

  switch (varName) {
    case 'NOW':
      return now;
    case 'TODAY':
      return new Date(now.getFullYear(), now.getMonth(), now.getDate(), 0, 0, 0);
    case 'YESTERDAY': {
      const yesterday = new Date(now);
      yesterday.setDate(yesterday.getDate() - 1);
      return new Date(yesterday.getFullYear(), yesterday.getMonth(), yesterday.getDate(), 0, 0, 0);
    }
    case 'TOMORROW': {
      const tomorrow = new Date(now);
      tomorrow.setDate(tomorrow.getDate() + 1);
      return new Date(tomorrow.getFullYear(), tomorrow.getMonth(), tomorrow.getDate(), 0, 0, 0);
    }
    case 'DATE':
    case 'TIME':
    case 'TIMESTAMP':
    case 'YEAR':
    case 'MONTH':
    case 'DAY':
      return now;
    default:
      return now;
  }
};

// Formatta il valore finale
const formatValue = (date, varName) => {
  switch (varName) {
    case 'DATE':
      return formatDate(date);
    case 'TIME':
      return formatTime(date);
    case 'TIMESTAMP':
      return String(date.getTime());
    case 'YEAR':
      return String(date.getFullYear());
    case 'MONTH':
      return String(date.getMonth() + 1).padStart(2, '0');
    case 'DAY':
      return String(date.getDate()).padStart(2, '0');
    default:
      return formatDateTime(date);
  }
};

/**
 * Processa una stringa sostituendo le variabili template con i valori reali
 * @param {string} text - Testo con variabili template
 * @param {Date} [referenceDate] - Data di riferimento (default: now)
 * @returns {string} - Testo con variabili sostituite
 */
export const processTemplateVariables = (text, referenceDate = null) => {
  if (!text || typeof text !== 'string') return text;
  
  return text.replace(TEMPLATE_REGEX, (match, varName, offset) => {
    try {
      // Passa referenceDate a getBaseDate — usata come "now" di riferimento
      // per tutte le variabili (NOW, TODAY, YESTERDAY, TOMORROW, DATE, ecc.)
      let date = getBaseDate(varName, referenceDate);

      // Applica offset se presente
      date = applyOffset(date, offset);
      
      // Formatta e ritorna
      return formatValue(date, varName);
    } catch (e) {
      console.error('Errore processando variabile template:', match, e);
      return match; // Ritorna originale se errore
    }
  });
};

/**
 * Verifica se una stringa contiene variabili template
 * @param {string} text - Testo da verificare
 * @returns {boolean}
 */
export const hasTemplateVariables = (text) => {
  if (!text || typeof text !== 'string') return false;
  TEMPLATE_REGEX.lastIndex = 0; // Reset regex state
  return TEMPLATE_REGEX.test(text);
};

/**
 * Estrae tutte le variabili template da una stringa
 * @param {string} text - Testo da analizzare
 * @returns {Array<{match: string, variable: string, offset: string|null}>}
 */
export const extractTemplateVariables = (text) => {
  if (!text || typeof text !== 'string') return [];
  
  const variables = [];
  let match;
  
  TEMPLATE_REGEX.lastIndex = 0;
  while ((match = TEMPLATE_REGEX.exec(text)) !== null) {
    variables.push({
      match: match[0],
      variable: match[1],
      offset: match[2] || null
    });
  }
  
  return variables;
};

/**
 * Lista delle variabili disponibili per l'autocomplete
 */
export const AVAILABLE_VARIABLES = [
  { name: '{{NOW}}', description: 'Data/ora corrente', example: '2026-01-27 06:30' },
  { name: '{{NOW-1h}}', description: 'Un\'ora fa', example: '2026-01-27 05:30' },
  { name: '{{NOW-2h}}', description: 'Due ore fa', example: '2026-01-27 04:30' },
  { name: '{{NOW+1h}}', description: 'Tra un\'ora', example: '2026-01-27 07:30' },
  { name: '{{NOW-1d}}', description: 'Ieri stessa ora', example: '2026-01-26 06:30' },
  { name: '{{TODAY}}', description: 'Oggi alle 00:00', example: '2026-01-27 00:00' },
  { name: '{{YESTERDAY}}', description: 'Ieri alle 00:00', example: '2026-01-26 00:00' },
  { name: '{{TOMORROW}}', description: 'Domani alle 00:00', example: '2026-01-28 00:00' },
  { name: '{{DATE}}', description: 'Solo data', example: '2026-01-27' },
  { name: '{{TIME}}', description: 'Solo ora', example: '06:30' },
  { name: '{{TIMESTAMP}}', description: 'Unix timestamp ms', example: '1769504400000' },
  { name: '{{YEAR}}', description: 'Anno corrente', example: '2026' },
  { name: '{{MONTH}}', description: 'Mese corrente', example: '01' },
  { name: '{{DAY}}', description: 'Giorno corrente', example: '27' },
];

export default {
  processTemplateVariables,
  hasTemplateVariables,
  extractTemplateVariables,
  AVAILABLE_VARIABLES
};
