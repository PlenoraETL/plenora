/**
 * transforms/index.js - Export centralizzato
 */

// Operations e utilities
export * from './transformOperations';
export { useTransformValidation } from './useTransformValidation';

// Base configs
export { default as JoinConfig } from './base/JoinConfig';
export { default as FormulaConfig } from './base/FormulaConfig';
export { default as FilterConfig } from './base/FilterConfig';
export { default as AggregateConfig } from './base/AggregateConfig';
export { default as ConcatConfig } from './base/ConcatConfig';
export { default as SortConfig } from './base/SortConfig';
export { default as DistinctConfig } from './base/DistinctConfig';
export { default as RenameConfig } from './base/RenameConfig';
export { default as DropColumnsConfig } from './base/DropColumnsConfig';
export { default as FillNaConfig } from './base/FillNaConfig';
export { default as ReplaceConfig } from './base/ReplaceConfig';
export { default as SplitColumnConfig } from './base/SplitColumnConfig';

// Spatial configs
export { default as SpatialJoinConfig } from './spatial/SpatialJoinConfig';
export { default as BufferConfig } from './spatial/BufferConfig';
export { default as CentroidConfig } from './spatial/CentroidConfig';
export { default as AreaConfig } from './spatial/AreaConfig';
export { default as ClipConfig } from './spatial/ClipConfig';
export { default as DissolveConfig } from './spatial/DissolveConfig';
export { default as TwoInputSpatialConfig } from './spatial/TwoInputSpatialConfig';
export { default as SimplifyConfig } from './spatial/SimplifyConfig';
export { default as ConvexHullConfig } from './spatial/ConvexHullConfig';
export { default as ReprojectConfig } from './spatial/ReprojectConfig';

// Mappa operazione -> componente config
export const configComponents = {
  // Base
  join: 'JoinConfig',
  formula: 'FormulaConfig',
  filter: 'FilterConfig',
  aggregate: 'AggregateConfig',
  concat: 'ConcatConfig',
  sort: 'SortConfig',
  distinct: 'DistinctConfig',
  rename: 'RenameConfig',
  drop_columns: 'DropColumnsConfig',
  fill_na: 'FillNaConfig',
  replace: 'ReplaceConfig',
  split_column: 'SplitColumnConfig',
  // Spatial
  sjoin: 'SpatialJoinConfig',
  geo_buffer: 'BufferConfig',
  geo_centroid: 'CentroidConfig',
  geo_area: 'AreaConfig',
  geo_clip: 'ClipConfig',
  geo_dissolve: 'DissolveConfig',
  geo_intersection: 'TwoInputSpatialConfig',
  geo_union: 'TwoInputSpatialConfig',
  geo_difference: 'TwoInputSpatialConfig',
  geo_simplify: 'SimplifyConfig',
  geo_convex_hull: 'ConvexHullConfig',
  geo_reproject: 'ReprojectConfig'
};
