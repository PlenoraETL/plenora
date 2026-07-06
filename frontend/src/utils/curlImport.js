const HEADER_OPTION_NAMES = new Set(['--header', '--header=']);
const DATA_OPTION_NAMES = new Set([
  '--data',
  '--data-raw',
  '--data-binary',
  '--data-ascii',
  '--data-urlencode',
]);
const SKIP_VALUE_OPTIONS = new Set([
  '--connect-timeout',
  '--max-time',
  '--retry',
  '--proxy',
  '--cacert',
  '--cert',
  '--key',
  '--output',
  '--request-target',
  '-o',
]);

const SUPPORTED_METHODS = new Set(['GET', 'POST', 'PUT', 'PATCH', 'DELETE']);

export function tokenizeCurlCommand(command) {
  const normalized = String(command || '')
    .replace(/\\\r?\n/g, ' ')
    .replace(/`\r?\n/g, ' ')
    .replace(/\^\r?\n/g, ' ');
  const tokens = [];
  let current = '';
  let quote = null;
  let escaped = false;

  for (const ch of normalized) {
    if (escaped) {
      current += ch;
      escaped = false;
      continue;
    }
    if (ch === '\\' && quote !== "'") {
      escaped = true;
      continue;
    }
    if (quote) {
      if (ch === quote) quote = null;
      else current += ch;
      continue;
    }
    if (ch === '"' || ch === "'") {
      quote = ch;
      continue;
    }
    if (/\s/.test(ch)) {
      if (current) {
        tokens.push(current);
        current = '';
      }
      continue;
    }
    current += ch;
  }

  if (escaped) current += '\\';
  if (quote) throw new Error('Comando cURL non valido: virgolette non chiuse');
  if (current) tokens.push(current);
  return tokens;
}

export function parseCurlCommand(command) {
  const tokens = tokenizeCurlCommand(command);
  if (tokens.length === 0) {
    throw new Error('Incolla un comando cURL');
  }
  if (tokens[0].toLowerCase() === 'curl') {
    tokens.shift();
  }

  let rawUrl = '';
  let method = '';
  let auth = null;
  let useGetWithData = false;
  const headers = {};
  const dataParts = [];
  const warnings = [];
  let allowRedirects = false;

  for (let i = 0; i < tokens.length; i += 1) {
    const token = tokens[i];
    if (token === '--') continue;

    const requestValue = readOptionValue(tokens, i, token, ['-X', '--request']);
    if (requestValue) {
      method = requestValue.value.toUpperCase();
      i = requestValue.index;
      continue;
    }

    const urlValue = readOptionValue(tokens, i, token, ['--url']);
    if (urlValue) {
      rawUrl = urlValue.value;
      i = urlValue.index;
      continue;
    }

    const headerValue = readOptionValue(tokens, i, token, ['-H', ...HEADER_OPTION_NAMES]);
    if (headerValue) {
      const parsedHeader = parseHeader(headerValue.value);
      if (parsedHeader) headers[parsedHeader.name] = parsedHeader.value;
      else warnings.push(`Header ignorato: ${headerValue.value}`);
      i = headerValue.index;
      continue;
    }

    const dataValue = readOptionValue(tokens, i, token, ['-d', ...DATA_OPTION_NAMES]);
    if (dataValue) {
      dataParts.push(dataValue.value);
      i = dataValue.index;
      continue;
    }

    const userValue = readOptionValue(tokens, i, token, ['-u', '--user']);
    if (userValue) {
      const [username, ...rest] = userValue.value.split(':');
      auth = { type: 'basic_auth', username, password: rest.join(':') };
      i = userValue.index;
      continue;
    }

    const userAgentValue = readOptionValue(tokens, i, token, ['-A', '--user-agent']);
    if (userAgentValue) {
      headers['User-Agent'] = userAgentValue.value;
      i = userAgentValue.index;
      continue;
    }

    const cookieValue = readOptionValue(tokens, i, token, ['-b', '--cookie']);
    if (cookieValue) {
      headers.Cookie = cookieValue.value;
      i = cookieValue.index;
      continue;
    }

    if (token === '-G' || token === '--get') {
      useGetWithData = true;
      continue;
    }
    if (token === '-L' || token === '--location') {
      allowRedirects = true;
      continue;
    }
    if (token === '-k' || token === '--insecure') {
      warnings.push('--insecure non importato: la policy TLS resta gestita dal backend');
      continue;
    }
    if (SKIP_VALUE_OPTIONS.has(token)) {
      i += 1;
      continue;
    }
    if (token.startsWith('--') && token.includes('=')) {
      const [name] = token.split('=', 1);
      if (SKIP_VALUE_OPTIONS.has(name)) continue;
    }
    if (token.startsWith('-')) continue;
    if (!rawUrl) rawUrl = token;
  }

  if (!rawUrl) throw new Error('Comando cURL senza URL');

  const bodyText = dataParts.join('&');
  const hasBody = dataParts.length > 0 && !useGetWithData;
  const finalMethod = normalizeMethod(method || (hasBody ? 'POST' : 'GET'));
  const { url, queryParams } = splitUrlQuery(rawUrl);
  const staticParameters = {};
  const request = {};

  let finalUrl = rawUrl;
  if (finalMethod === 'GET') {
    finalUrl = url;
    Object.assign(staticParameters, queryParams);
  }

  if (useGetWithData && bodyText) {
    Object.assign(staticParameters, parseFormBody(bodyText));
  } else if (hasBody) {
    applyBody(bodyText, headers, staticParameters, request);
  }

  const authFromHeaders = extractAuth(headers);
  if (!auth && authFromHeaders.auth) auth = authFromHeaders.auth;
  const customHeaders = authFromHeaders.headers;
  if (request.allow_redirects == null && allowRedirects) {
    request.allow_redirects = true;
  }

  return {
    url: finalUrl,
    method: finalMethod,
    auth: auth || { type: 'none' },
    custom_headers: customHeaders,
    static_parameters: staticParameters,
    parameters: buildParameters(finalUrl, staticParameters),
    request,
    name: suggestName(finalUrl),
    warnings,
  };
}

function readOptionValue(tokens, index, token, names) {
  for (const name of names) {
    if (name.endsWith('=') && token.startsWith(name)) {
      return { value: token.slice(name.length), index };
    }
    if (token === name) {
      return { value: tokens[index + 1] || '', index: index + 1 };
    }
    if (name.length === 2 && token.startsWith(name) && token.length > 2) {
      return { value: token.slice(2), index };
    }
    if (name.startsWith('--') && token.startsWith(`${name}=`)) {
      return { value: token.slice(name.length + 1), index };
    }
  }
  return null;
}

function parseHeader(rawHeader) {
  const separator = rawHeader.indexOf(':');
  if (separator <= 0) return null;
  return {
    name: rawHeader.slice(0, separator).trim(),
    value: rawHeader.slice(separator + 1).trim(),
  };
}

function normalizeMethod(method) {
  const normalized = String(method || 'GET').toUpperCase();
  if (!SUPPORTED_METHODS.has(normalized)) {
    throw new Error(`Metodo HTTP non supportato dal connettore: ${normalized}`);
  }
  return normalized;
}

function splitUrlQuery(rawUrl) {
  const hashIndex = rawUrl.indexOf('#');
  const beforeHash = hashIndex >= 0 ? rawUrl.slice(0, hashIndex) : rawUrl;
  const queryIndex = beforeHash.indexOf('?');
  if (queryIndex < 0) return { url: beforeHash, queryParams: {} };
  return {
    url: beforeHash.slice(0, queryIndex),
    queryParams: parseFormBody(beforeHash.slice(queryIndex + 1)),
  };
}

function applyBody(bodyText, headers, staticParameters, request) {
  const contentType = getHeader(headers, 'content-type').toLowerCase();
  const trimmed = bodyText.trim();

  if (contentType.includes('application/x-www-form-urlencoded')) {
    Object.assign(staticParameters, parseFormBody(bodyText));
    request.body_type = 'form-urlencoded';
    return;
  }

  if (contentType.includes('json') || looksLikeJson(trimmed)) {
    try {
      const parsed = JSON.parse(trimmed);
      if (parsed && typeof parsed === 'object' && !Array.isArray(parsed)) {
        Object.assign(staticParameters, parsed);
        request.body_type = 'json';
        return;
      }
    } catch (_err) {
      // Fall through to raw body: the user can still edit it after import.
    }
  }

  if (looksLikeFormBody(bodyText)) {
    Object.assign(staticParameters, parseFormBody(bodyText));
    request.body_type = 'form-urlencoded';
    return;
  }

  request.body_type = 'raw';
  request.raw_body = bodyText;
}

function parseFormBody(bodyText) {
  const params = {};
  const search = new URLSearchParams(bodyText);
  search.forEach((value, key) => {
    params[key] = value;
  });
  return params;
}

function looksLikeJson(value) {
  return value.startsWith('{') || value.startsWith('[');
}

function looksLikeFormBody(value) {
  return /^[^=&]+=[\s\S]*$/.test(value) && !looksLikeJson(value.trim());
}

function getHeader(headers, headerName) {
  const match = Object.keys(headers).find(
    key => key.toLowerCase() === headerName.toLowerCase(),
  );
  return match ? headers[match] : '';
}

function extractAuth(headers) {
  const nextHeaders = { ...headers };
  const authorizationKey = Object.keys(nextHeaders).find(
    key => key.toLowerCase() === 'authorization',
  );
  if (authorizationKey) {
    const value = nextHeaders[authorizationKey];
    delete nextHeaders[authorizationKey];
    if (/^bearer\s+/i.test(value)) {
      return {
        auth: { type: 'bearer', token: value.replace(/^bearer\s+/i, '').trim() },
        headers: nextHeaders,
      };
    }
    if (/^basic\s+/i.test(value)) {
      const decoded = decodeBase64(value.replace(/^basic\s+/i, '').trim());
      const [username, ...rest] = decoded.split(':');
      return {
        auth: { type: 'basic_auth', username, password: rest.join(':') },
        headers: nextHeaders,
      };
    }
    nextHeaders.Authorization = value;
  }

  const apiKeyHeader = Object.keys(nextHeaders).find(key =>
    ['x-api-key', 'api-key', 'apikey', 'ocp-apim-subscription-key']
      .includes(key.toLowerCase()),
  );
  if (apiKeyHeader) {
    const keyValue = nextHeaders[apiKeyHeader];
    delete nextHeaders[apiKeyHeader];
    return {
      auth: {
        type: 'api_key',
        location: 'header',
        key_name: apiKeyHeader,
        key_value: keyValue,
      },
      headers: nextHeaders,
    };
  }

  return { auth: null, headers: nextHeaders };
}

function decodeBase64(value) {
  if (typeof atob === 'function') return atob(value);
  return '';
}

function buildParameters(url, staticParameters) {
  const mapped = extractPathParamNames(url).map(name => ({
    name,
    type: 'string',
    required: true,
    mode: 'mapped',
  }));
  const fixed = Object.entries(staticParameters).map(([name, value]) => ({
    name,
    type: inferParamType(value),
    required: false,
    mode: 'fixed',
  }));
  return [...mapped, ...fixed];
}

function extractPathParamNames(url) {
  const names = new Set();
  String(url).replace(/\{([^{}]+)\}/g, (_match, name) => {
    if (name) names.add(name);
    return '';
  });
  return [...names];
}

function inferParamType(value) {
  if (typeof value === 'number') return 'number';
  if (typeof value === 'boolean') return 'boolean';
  return 'string';
}

function suggestName(url) {
  const withoutQuery = String(url).split('?')[0];
  const parts = withoutQuery.replace(/^https?:\/\//i, '').split('/').filter(Boolean);
  if (parts.length === 0) return 'API importata';
  return parts.slice(0, 2).join(' ');
}
