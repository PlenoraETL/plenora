/**
 * Componenti riutilizzabili per DataFlow Manager
 * 
 * Import esempio:
 * import { ExecutionModal, PreviewModal, EditorSidebar } from '../components';
 */

// Modal
export { default as ExecutionModal } from './ExecutionModal';
export { default as PreviewModal } from './PreviewModal';
export { default as JoinModal } from './JoinModal';
export { default as FilterModal } from './FilterModal';
export { default as ColumnBuilderModal } from './ColumnBuilderModal';

// Sidebar
export { default as ProjectsSidebar } from './ProjectsSidebar';
export { default as FilesSidebar } from './FilesSidebar';
export { default as QuickActions } from './QuickActions';
export { default as EditorSidebar } from './EditorSidebar';

// Nodi React Flow
export { default as TableNode } from './TableNode';
export { default as ResultNode } from './ResultNode';
export { default as TransformNode } from './TransformNode';
export { default as ProjectSourceNode } from './ProjectSourceNode';
export { default as FileSourceNode } from './FileSourceNode';
export { default as ApiEnrichNode } from './ApiEnrichNode';

// Altri
export { default as QueryPreview } from './QueryPreview';
