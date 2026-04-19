/**
 * Componenti riutilizzabili per DataFlow Manager
 *
 * Import esempio:
 * import { ExecutionModal, PreviewModal, EditorSidebar } from '../components';
 */

// Modal
export { default as ExecutionModal } from './modals/ExecutionModal';
export { default as PreviewModal } from './modals/PreviewModal';
export { default as JoinModal } from './modals/JoinModal';
export { default as FilterModal } from './modals/FilterModal';
export { default as ColumnBuilderModal } from './modals/ColumnBuilderModal';

// Sidebar e pannelli
export { default as ProjectsSidebar } from './panels/ProjectsSidebar';
export { default as FilesSidebar } from './panels/FilesSidebar';
export { default as QuickActions } from './panels/QuickActions';
export { default as EditorSidebar } from './panels/EditorSidebar';
export { default as ApiEnrichNode } from './panels/ApiEnrichNode';
export { default as QueryPreview } from './panels/QueryPreview';

// Nodi canvas
export { default as TableNode } from './nodes/TableNode';
export { default as ResultNode } from './nodes/ResultNode';
export { default as ProjectSourceNode } from './nodes/ProjectSourceNode';
export { default as FileSourceNode } from './nodes/FileSourceNode';
export { default as CaricanNode } from './nodes/CaricanNode';

// Transforms (re-export da sottocartella)
export { default as TransformNode } from './transforms/TransformNode';
