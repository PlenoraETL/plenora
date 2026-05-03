/**
 * JsonTreeNode.jsx — Renderer ricorsivo del JSON tree
 *
 * Estratto da `panels/ResponseConfigBuilder.jsx` in `FE-SPLIT-003A`.
 * Componente presentational: riceve l'`AbstractJsonNode` (`{name,
 * type, children?, sample?, count?, fullPath}`) + Set di `expandedPaths`
 * + array `iterateOn` / `mappings` + 3 callback toggle.
 *
 * Indent calcolato runtime via `paddingLeft = depth * 20px` (DYNAMIC,
 * resta inline). Stili (`json-tree-node*`) restano in
 * `panels/ResponseConfigBuilder.css` per non spezzare lo scope shared
 * con il parent builder.
 *
 * Helper `formatSampleValue` co-locato qui perche' usato solo dal tree
 * per il preview "Es: ..." sui nodi scalari.
 */
import React from 'react';

function formatSampleValue(value) {
  if (value === null) return 'null';
  if (typeof value === 'string') {
    return value.length > 30 ? `"${value.substring(0, 30)}..."` : `"${value}"`;
  }
  return String(value);
}

export { formatSampleValue };

export default function JsonTreeNode({
  node,
  path,
  depth,
  expandedPaths,
  iterateOn,
  mappings,
  onToggleExpand,
  onToggleIterate,
  onToggleMapping,
}) {
  const isExpanded = expandedPaths.has(path);
  const isArray = node.type === 'array';
  const isObject = node.type === 'object';
  const isExpandable = isArray || isObject;

  const isIterated = iterateOn.some(i => i.path === (path || ''));
  const isSelected = mappings.some(m => m.path === node.fullPath);

  const indent = depth * 20;

  return (
    <div className="json-tree-node">
      <div
        className={`json-tree-node__row ${isIterated ? 'json-tree-node__row--iterated' : ''} ${isSelected ? 'json-tree-node__row--selected' : ''}`}
        style={{ paddingLeft: `${indent}px` }}
      >
        {/* Expand/Collapse */}
        {isExpandable ? (
          <button type="button"
            className="json-tree-node__toggle"
            onClick={() => onToggleExpand(path)}
          >
            {isExpanded ? '▼' : '▶'}
          </button>
        ) : (
          <span className="json-tree-node__toggle json-tree-node__toggle--empty"></span>
        )}

        {/* Nome campo */}
        <span className="json-tree-node__name">{node.name || '(root)'}</span>

        {/* Badge tipo */}
        <span className={`json-tree-node__badge json-tree-node__badge--${node.type}`}>
          {node.type}
          {isArray && node.count !== undefined && ` [${node.count}]`}
        </span>

        {/* Azioni */}
        <div className="json-tree-node__actions">
          {/* Toggle iterate (solo per array) */}
          {isArray && (
            <button type="button"
              className={`json-tree-node__action ${isIterated ? 'json-tree-node__action--active' : ''}`}
              onClick={() => onToggleIterate(path || '', node.name)}
              title={isIterated ? 'Non esplodere questo array' : 'Esplodi questo array'}
            >
              {isIterated ? '🔄 Esplodi' : '💫 Esplodi'}
            </button>
          )}

          {/* Toggle mapping (solo per scalari) */}
          {!isExpandable && (
            <label className="json-tree-node__checkbox">
              <input
                type="checkbox"
                checked={isSelected}
                onChange={() => onToggleMapping(node.fullPath, node.name, node.type)}
              />
              <span>Estrai</span>
            </label>
          )}
        </div>

        {/* Sample value per scalari */}
        {!isExpandable && node.sample !== undefined && (
          <span className="json-tree-node__sample">
            Es: {formatSampleValue(node.sample)}
          </span>
        )}
      </div>

      {/* Children */}
      {isExpanded && node.children && (
        <div className="json-tree-node__children">
          {node.children.map((child, idx) => (
            <JsonTreeNode
              key={child.name || idx}
              node={child}
              path={path ? `${path}.${child.name}` : child.name}
              depth={depth + 1}
              expandedPaths={expandedPaths}
              iterateOn={iterateOn}
              mappings={mappings}
              onToggleExpand={onToggleExpand}
              onToggleIterate={onToggleIterate}
              onToggleMapping={onToggleMapping}
            />
          ))}
        </div>
      )}
    </div>
  );
}
