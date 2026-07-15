/**
 * Deterministic live-schema rules for registry transforms that were missing
 * from useColumnPropagation. Keep these rules aligned with
 * backend/app/core/projects/schema_*_handlers.py.
 *
 * `null` means "not handled here"; an empty array is a valid output schema.
 */

const clone = columns => (columns || []).map(col => ({ ...col }));
const names = columns => new Set((columns || []).map(col => col.name));

function addColumn(columns, name, type = 'str') {
  if (!name || columns.some(col => col.name === name)) return columns;
  return [...columns, { name, type, is_calculated: true }];
}

function normalizeStringList(raw) {
  if (typeof raw === 'string') {
    return raw.split(',').map(value => value.trim()).filter(Boolean);
  }
  if (Array.isArray(raw)) {
    return raw.map(value => String(value ?? '').trim()).filter(Boolean);
  }
  return [];
}

function normalizeOutputColumns(raw) {
  if (typeof raw === 'string') {
    const name = raw.trim();
    return name ? [{ name, type: 'str' }] : [];
  }
  if (!Array.isArray(raw)) return [];
  return raw.flatMap(entry => {
    if (typeof entry === 'string') {
      const name = entry.trim();
      return name ? [{ name, type: 'str' }] : [];
    }
    const name = String(entry?.name || '').trim();
    return name ? [{ name, type: entry?.type || 'str' }] : [];
  });
}

function isValidPythonRegex(pattern) {
  // Il runtime usa Python `re`, il browser JavaScript. Convertiamo le
  // estensioni Python piu' comuni solo per validare la sintassi usata
  // nella propagazione; l'espressione originale resta invariata.
  const javascriptPattern = pattern
    .replace(/\(\?P<([A-Za-z_][A-Za-z0-9_]*)>/g, '(?<$1>')
    .replace(/\(\?P=([A-Za-z_][A-Za-z0-9_]*)\)/g, '\\k<$1>')
    .replace(/^\(\?[aiLmsux-]+\)/, '')
    .replace(/\(\?[aiLmsux-]+:/g, '(?:');
  try {
    new RegExp(javascriptPattern);
    return true;
  } catch {
    return false;
  }
}

function sourceSchemas(inputColumns, sourcesColumns) {
  if (Array.isArray(sourcesColumns) && sourcesColumns.length > 0) {
    return sourcesColumns.map(clone);
  }
  return [clone(inputColumns)];
}

function combineOverlay(left, right, suffix = '_2', includeRightGeometry = false) {
  const result = clone(left);
  const leftNames = names(left);
  right.forEach(col => {
    if (!includeRightGeometry && String(col.type || '').startsWith('geometry')) return;
    result.push({ ...col, name: leftNames.has(col.name) ? `${col.name}${suffix}` : col.name });
  });
  return result;
}

function reshapeBuilder(input, config, geometryType) {
  const groupName = config.group_column || '';
  if (!groupName) return clone(input);
  const source = input.find(col => col.name === groupName);
  const result = [{ ...(source || { name: groupName, type: 'str' }) }];
  const outputName = config.output_column || 'geometry';
  return outputName !== groupName
    ? addColumn(result, outputName, geometryType)
    : result;
}

function tableDiff(left, right, config) {
  const leftKeys = normalizeStringList(config.left_keys);
  const rightKeys = normalizeStringList(config.right_keys);
  const leftByName = new Map(left.map(col => [col.name, col]));
  const rightByName = new Map(right.map(col => [col.name, col]));
  if (
    leftKeys.length === 0
    || leftKeys.length !== rightKeys.length
    || leftKeys.some(key => !leftByName.has(key))
    || rightKeys.some(key => !rightByName.has(key))
  ) return clone(left);

  const rightRename = new Map(rightKeys.map((key, index) => [key, leftKeys[index]]));
  const normalizedRight = right.map(col => ({
    ...col,
    name: rightRename.get(col.name) || col.name,
  }));
  const keySet = new Set(leftKeys);
  const leftValues = left.filter(col => !keySet.has(col.name));
  const rightValues = normalizedRight.filter(col => !keySet.has(col.name));
  const rightValueNames = names(rightValues);
  const leftValueNames = names(leftValues);
  let common = leftValues.filter(col => rightValueNames.has(col.name));
  const compare = normalizeStringList(config.compare_columns);
  if (compare.length > 0) common = compare.flatMap(name => {
    const col = common.find(item => item.name === name);
    return col ? [col] : [];
  });
  const result = [
    ...leftKeys.map(key => ({ ...leftByName.get(key) })),
    ...clone(common),
    ...clone(leftValues.filter(col => !rightValueNames.has(col.name))),
    ...clone(rightValues.filter(col => !leftValueNames.has(col.name))),
  ];
  return ['_diff_status', '_diff_columns', '_diff_old_values']
    .reduce((cols, name) => addColumn(cols, name, 'str'), result);
}

export const EXTENDED_SCHEMA_OPS = Object.freeze([
  'add_row_number',
  'bin',
  'cross_join',
  'date_extract',
  'flatten_json',
  'geo_bounds_extractor',
  'geo_clip',
  'geo_count_points_in_polygons',
  'geo_difference',
  'geo_dissolve',
  'geo_distance',
  'geo_intersection',
  'geo_line_builder',
  'geo_nearest',
  'geo_overlay',
  'geo_polygon_builder',
  'geo_symmetric_difference',
  'geo_to_wkt',
  'geo_union',
  'geo_voronoi',
  'geo_within',
  'lookup',
  'mask_data',
  'md5_hash',
  'melt',
  'reorder_columns',
  'sjoin',
  'split_column',
  'statistics',
  'string_extract',
  'string_length',
  'string_pad',
  'table_diff',
  'text_normalize',
  'transpose',
  'type_cast',
  'uuid_generator',
  'window_function',
]);

// Operazioni dichiaratamente schema-invarianti. La lista e' esplicita:
// il contract registry fallisce quando viene aggiunto un nuovo transform,
// anche se il suo output e' marcato `same`, finche' non viene classificato.
export const SCHEMA_PASSTHROUGH_OPS = Object.freeze([
  'dedup_advanced',
  'fill_na',
  'geo_clean_topology',
  'geo_explode',
  'replace',
  'sample',
]);

export function applyExtendedTransformSchema(
  operation,
  config = {},
  inputColumns = [],
  sourcesColumns = [],
) {
  if (!EXTENDED_SCHEMA_OPS.includes(operation)) return null;
  const sources = sourceSchemas(inputColumns, sourcesColumns);
  const left = sources[0] || [];
  const right = sources[1] || [];
  let result = clone(inputColumns);

  switch (operation) {
    case 'split_column':
      return normalizeStringList(config.new_columns)
        .reduce((cols, name) => addColumn(cols, name, 'str'), result);

    case 'md5_hash':
      return addColumn(result, config.output_column || 'md5_hash', 'str');

    case 'mask_data':
      if (config.overwrite) return result;
      return (Array.isArray(config.maskings) ? config.maskings : []).reduce(
        (cols, mask) => addColumn(cols, `${mask?.column || ''}_masked`, 'str'),
        result,
      );

    case 'type_cast': {
      const typeMap = { str: 'str', int: 'int', float: 'real', bool: 'bool', date: 'date', datetime: 'datetime' };
      return result.map(col => col.name === config.column
        ? { ...col, type: typeMap[config.target_type] || 'str', is_calculated: true }
        : col);
    }

    case 'text_normalize':
      if (config.overwrite !== false) return result;
      return normalizeStringList(config.columns)
        .reduce((cols, name) => addColumn(cols, `${name}_norm`, 'str'), result);

    case 'add_row_number':
      return addColumn(result, config.output_column || 'row_number', 'int');

    case 'uuid_generator':
      return addColumn(result, config.output_column || 'uuid', 'str');

    case 'string_length': {
      const column = config.column || '';
      if (!column || !names(result).has(column)) return result;
      return addColumn(result, config.output_column || `${column}_length`, 'int');
    }

    case 'date_extract': {
      const column = config.column || '';
      if (!column) return result;
      const parts = config.parts == null || !(typeof config.parts === 'string' || Array.isArray(config.parts))
        ? ['year']
        : normalizeStringList(config.parts);
      const valid = new Set(['year', 'month', 'day', 'quarter', 'weekday', 'week', 'hour', 'minute', 'second']);
      const prefix = config.prefix || `${column}_`;
      return parts.reduce((cols, part) => {
        const normalized = part.toLowerCase();
        return valid.has(normalized) ? addColumn(cols, `${prefix}${normalized}`, 'int') : cols;
      }, result);
    }

    case 'string_extract': {
      const column = config.column || '';
      const pattern = config.pattern || '';
      if (!column || !pattern || !names(result).has(column) || !isValidPythonRegex(pattern)) return result;
      const groupNames = [...pattern.matchAll(/\(\?P?<([A-Za-z_][A-Za-z0-9_]*)>/g)]
        .map(match => match[1]);
      if (groupNames.length > 0) {
        return groupNames.reduce((cols, name) => addColumn(cols, name, 'str'), result);
      }
      return addColumn(result, config.output_column || `${column}_extracted`, 'str');
    }

    case 'bin': {
      const column = config.column || '';
      if (!column || !names(result).has(column)) return result;
      return addColumn(result, config.output_column || `${column}_bin`, 'str');
    }

    case 'string_pad': {
      const column = config.column || '';
      if (!column || !names(result).has(column)) return result;
      const target = config.output_column || column;
      if (names(result).has(target)) {
        return result.map(col => col.name === target
          ? { ...col, type: 'str', is_calculated: true }
          : col);
      }
      return addColumn(result, target, 'str');
    }

    case 'lookup': {
      const column = config.column || '';
      if (!column || !config.mapping || !names(result).has(column)) return result;
      const target = config.output_column || column;
      const source = result.find(col => col.name === column);
      return target === column ? result : addColumn(result, target, source?.type || 'str');
    }

    case 'statistics': {
      const column = config.column || '';
      if (!column || !names(result).has(column)) return result;
      const stats = config.stats == null
        ? ['count', 'min', 'max', 'mean', 'median', 'std']
        : normalizeStringList(config.stats);
      const valid = new Set(['count', 'min', 'max', 'sum', 'mean', 'median', 'std', 'var', 'q25', 'q75']);
      const prefix = config.output_prefix || `${column}_`;
      return stats.reduce((cols, stat) => valid.has(stat)
        ? addColumn(cols, `${prefix}${stat}`, stat === 'count' ? 'int' : 'real')
        : cols, result);
    }

    case 'window_function': {
      const column = config.column || '';
      const fn = config.function || 'rank';
      const valid = new Set(['rank', 'dense_rank', 'cumsum', 'cumcount', 'lag', 'lead', 'pct_change', 'running_mean']);
      const source = result.find(col => col.name === column);
      if (!source || !valid.has(fn)) return result;
      const type = ['rank', 'dense_rank', 'pct_change', 'running_mean'].includes(fn)
        ? 'real'
        : (fn === 'cumcount' ? 'int' : source.type || 'str');
      return addColumn(result, config.output_column || `${column}_${fn}`, type);
    }

    case 'flatten_json': {
      const prefix = config.prefix || (config.column ? `${config.column}_` : '');
      return normalizeOutputColumns(config.output_columns).reduce(
        (cols, col) => addColumn(cols, `${prefix}${col.name}`, col.type),
        result,
      );
    }

    case 'transpose': {
      const output = normalizeOutputColumns(config.output_columns);
      if (output.length === 0) return clone(left);
      const idColumn = left.find(col => col.name === config.id_column);
      return output.reduce(
        (cols, col) => addColumn(cols, col.name, col.type),
        idColumn ? [{ ...idColumn }] : [],
      );
    }

    case 'melt': {
      const byName = new Map(left.map(col => [col.name, col]));
      const ids = normalizeStringList(config.id_columns).filter(name => byName.has(name));
      const configuredValues = normalizeStringList(config.value_columns);
      const values = (configuredValues.length > 0 ? configuredValues : left.map(col => col.name).filter(name => !ids.includes(name)))
        .filter(name => byName.has(name));
      const valueTypes = new Set(values.map(name => byName.get(name)?.type || 'str'));
      let output = ids.map(name => ({ ...byName.get(name) }));
      output = addColumn(output, config.var_name || 'variable', 'str');
      return addColumn(output, config.value_name || 'value', valueTypes.size === 1 ? [...valueTypes][0] : 'str');
    }

    case 'reorder_columns': {
      const requested = normalizeStringList(config.columns).filter(name => names(left).has(name));
      const ordered = requested.length > 0
        ? [...requested, ...left.map(col => col.name).filter(name => !requested.includes(name))]
        : (config.sort_alphabetical ? [...left.map(col => col.name)].sort() : left.map(col => col.name));
      const byName = new Map(left.map(col => [col.name, col]));
      return ordered.map(name => ({ ...byName.get(name) })).filter(Boolean);
    }

    case 'cross_join': {
      if (sources.length < 2) return clone(left);
      const collisions = new Set([...names(left)].filter(name => names(right).has(name)));
      return [
        ...left.map(col => ({ ...col, name: collisions.has(col.name) ? `${col.name}_x` : col.name })),
        ...right.map(col => ({ ...col, name: collisions.has(col.name) ? `${col.name}_y` : col.name })),
      ];
    }

    case 'sjoin':
      if (sources.length < 2) return clone(left);
      return addColumn(combineOverlay(left, right, '_right', true), 'index_right', 'int');

    case 'geo_clip':
      return clone(left);

    case 'geo_difference':
    case 'geo_intersection':
    case 'geo_overlay':
    case 'geo_symmetric_difference':
    case 'geo_union':
      return sources.length < 2 ? clone(left) : combineOverlay(left, right, '_2', false);

    case 'geo_dissolve': {
      const grouped = config.by_column
        ? clone(left.filter(col => col.name === config.by_column))
        : [];
      return addColumn(grouped, config.geom_column || 'geometry', 'geometry');
    }

    case 'geo_line_builder':
      return reshapeBuilder(left, config, 'geometry:line');

    case 'geo_polygon_builder':
      return reshapeBuilder(left, config, 'geometry:polygon');

    case 'geo_distance':
      return addColumn(clone(left), config.output_column || 'distance_m', 'real');

    case 'geo_to_wkt': {
      const mode = ['wkt', 'latlon', 'both'].includes(String(config.mode || '').toLowerCase())
        ? String(config.mode).toLowerCase()
        : 'wkt';
      let output = clone(left);
      if (mode === 'wkt' || mode === 'both') output = addColumn(output, config.wkt_column || 'geometry_wkt', 'str');
      if (mode === 'latlon' || mode === 'both') {
        output = addColumn(output, config.lat_column || 'latitude', 'real');
        output = addColumn(output, config.lon_column || 'longitude', 'real');
      }
      return output;
    }

    case 'geo_bounds_extractor':
      return ['min_x', 'min_y', 'max_x', 'max_y'].reduce(
        (cols, suffix) => addColumn(cols, `${config.prefix || ''}${suffix}`, 'real'),
        clone(left),
      );

    case 'geo_count_points_in_polygons': {
      let output = addColumn(clone(left), config.count_column || 'point_count', 'int');
      const aggregateColumn = config.aggregate_column || '';
      if (aggregateColumn) {
        const fn = ['sum', 'mean', 'min', 'max'].includes(config.aggregate_func)
          ? config.aggregate_func
          : 'sum';
        output = addColumn(output, `${aggregateColumn}_${fn}`, 'real');
      }
      return output;
    }

    case 'geo_nearest': {
      if (sources.length < 2) return clone(left);
      let output = clone(left);
      const leftNames = names(left);
      right.forEach(col => {
        if (String(col.type || '').startsWith('geometry')) return;
        output = addColumn(output, leftNames.has(col.name) ? `${col.name}_right` : col.name, col.type || 'str');
      });
      return addColumn(output, config.output_column || 'distance_m', 'real');
    }

    case 'geo_voronoi': {
      const geometryColumn = config.geom_column || 'geometry';
      return clone(left).map(col => col.name === geometryColumn
        ? { ...col, type: 'geometry:polygon', is_calculated: true }
        : col);
    }

    case 'geo_within':
      return clone(left);

    case 'table_diff':
      return tableDiff(left, right, config);

    default:
      return null;
  }
}
