'use client';

// Copyright (c) 2026 SiOrigin Co. Ltd.
// SPDX-License-Identifier: Apache-2.0

import { useEffect, useState, useCallback, useRef } from 'react';
import { useRouter, useParams } from 'next/navigation';
import { useTheme } from '@/lib/theme-context';
import { DOC_GENERATION_MODELS } from '@/lib/model-config';
import { ModelCombobox } from '@/components/ModelCombobox';
import { Modal } from '@/components/Modal';
import { useGenerationStore } from '@/lib/store';
import { Card } from '@/components/Card';
import { DropdownMenu } from '@/components/DropdownMenu';
import { useToast } from '@/components/Toast';
import { getThemeColors } from '@/lib/theme-colors';
import { triggerTaskRefresh, useGlobalTasks } from '@/lib/hooks/useGlobalTasks';
import { apiFetch, getApiClient } from '@/lib/api-client';
// Folder management components
import { FolderBreadcrumb } from '@/components/FolderBreadcrumb';
import { CreateFolderModal } from '@/components/CreateFolderModal';
import { MoveDocumentModal } from '@/components/MoveDocumentModal';
import { ContextMenu, ContextMenuIcons } from '@/components/ContextMenu';
import { SelectionToolbar } from '@/components/SelectionToolbar';
import { LassoSelector, useLassoSelection } from '@/components/LassoSelector';
import type { Folder, FolderStructure } from '@/types/folders';

interface Research {
  name: string;
  lastUpdated: string;
  metadata: {
    id?: string;
    referencesCount?: number;
    codeBlocksCount?: number;
    query?: string;
  };
}

interface ResearchGroup {
  name: string;
  items: Research[];
  isDuplicate: boolean;
}

export default function ResearchPage() {
  const router = useRouter();
  const params = useParams();
  const repoName = params.repo as string;
  const { theme } = useTheme();
  const colors = getThemeColors(theme);
  const { showToast } = useToast();
  const [researchList, setResearchList] = useState<Research[]>([]);
  const [loading, setLoading] = useState(true);
  const [searchQuery, setSearchQuery] = useState('');
  const [showAddResearchModal, setShowAddResearchModal] = useState(false);
  const [researchName, setResearchName] = useState('');
  const [customQuery, setCustomQuery] = useState('');
  const [isAdding, setIsAdding] = useState(false);
  const [addError, setAddError] = useState('');
  // Research template state
  const [selectedTemplate, setSelectedTemplate] = useState<string>('operator');
  const [templateLanguage, setTemplateLanguage] = useState<'zh' | 'en'>('zh');
  const [selectedModel, setSelectedModel] = useState<string>('');

  // Use centralized model configuration for research generation
  const availableModels = DOC_GENERATION_MODELS;

  // Research query templates
  const researchTemplates = {
    operator: {
      id: 'operator',
      name: '\u7B97\u5B50\u5206\u6790 / Operator Analysis',
      description: '\u6DF1\u5EA6\u5206\u6790\u7279\u5B9A\u7B97\u5B50/\u51FD\u6570\u7684\u5B9E\u73B0\u548C\u4F9D\u8D56',
      template_zh: `\u5206\u6790 [\u5728\u6B64\u8F93\u5165\u7B97\u5B50\u540D\u79F0] \u7684\u5B8C\u6574\u5B9E\u73B0\uFF1A
- \u51FD\u6570\u7B7E\u540D\u548C\u53C2\u6570\u8BF4\u660E
- \u4F9D\u8D56\u5173\u7CFB\u548C\u8C03\u7528\u94FE
- \u5E95\u5C42\u5B9E\u73B0\uFF08Triton/CUDA/C++ \u5185\u6838\uFF09
- \u6027\u80FD\u4F18\u5316\u7B56\u7565
- \u4F7F\u7528\u793A\u4F8B`,
      template_en: `Analyze the complete implementation of [enter operator name here]:
- Function signature and parameters
- Dependencies and call chains
- Low-level implementation (Triton/CUDA/C++ kernels)
- Performance optimization strategies
- Usage examples`,
    },
    research: {
      id: 'research',
      name: '\u6DF1\u5EA6\u7814\u7A76 / Deep Research',
      description: '\u5BF9\u67D0\u4E2A\u9886\u57DF/\u4E3B\u9898\u8FDB\u884C\u5168\u9762\u6DF1\u5EA6\u7814\u7A76',
      template_zh: `\u6DF1\u5EA6\u7814\u7A76 [\u5728\u6B64\u8F93\u5165\u7814\u7A76\u4E3B\u9898]\uFF1A
- \u6838\u5FC3\u6982\u5FF5\u548C\u8BBE\u8BA1\u7406\u5FF5
- \u67B6\u6784\u8BBE\u8BA1\u548C\u7EC4\u4EF6\u5173\u7CFB
- \u5173\u952E\u5B9E\u73B0\u7EC6\u8282
- \u6570\u636E\u6D41\u548C\u8C03\u7528\u94FE
- \u6700\u4F73\u5B9E\u8DF5\u548C\u4F7F\u7528\u6A21\u5F0F`,
      template_en: `Deep research on [enter topic here]:
- Core concepts and design philosophy
- Architecture and component relationships
- Key implementation details
- Data flow and call chains
- Best practices and usage patterns`,
    },
    architecture: {
      id: 'architecture',
      name: '\u67B6\u6784\u5206\u6790 / Architecture Analysis',
      description: '\u5206\u6790\u7CFB\u7EDF/\u6A21\u5757\u7684\u67B6\u6784\u8BBE\u8BA1\u548C\u8BBE\u8BA1\u6A21\u5F0F',
      template_zh: `\u5206\u6790 [\u5728\u6B64\u8F93\u5165\u6A21\u5757/\u7CFB\u7EDF\u540D\u79F0] \u7684\u67B6\u6784\u8BBE\u8BA1\uFF1A
- \u6574\u4F53\u67B6\u6784\u548C\u5206\u5C42\u7ED3\u6784
- \u6838\u5FC3\u7EC4\u4EF6\u548C\u804C\u8D23\u5212\u5206
- \u7EC4\u4EF6\u95F4\u901A\u4FE1\u548C\u6570\u636E\u6D41
- \u4F7F\u7528\u7684\u8BBE\u8BA1\u6A21\u5F0F
- \u6269\u5C55\u70B9\u548C\u63A5\u53E3\u8BBE\u8BA1`,
      template_en: `Analyze the architecture of [enter module/system name here]:
- Overall architecture and layering
- Core components and responsibilities
- Inter-component communication and data flow
- Design patterns used
- Extension points and interface design`,
    },
    custom: {
      id: 'custom',
      name: '\u81EA\u5B9A\u4E49 / Custom',
      description: '\u81EA\u7531\u8F93\u5165\u7814\u7A76\u9700\u6C42',
      template_zh: `[\u5728\u6B64\u8F93\u5165\u4F60\u7684\u7814\u7A76\u9700\u6C42]`,
      template_en: `[Enter your research requirements here]`,
    },
  };

  // Handle template selection
  const handleTemplateSelect = (templateId: string) => {
    setSelectedTemplate(templateId);
    const template = researchTemplates[templateId as keyof typeof researchTemplates];
    if (template) {
      const templateText = templateLanguage === 'zh' ? template.template_zh : template.template_en;
      setCustomQuery(templateText);
    }
  };

  // Handle language change
  const handleLanguageChange = (lang: 'zh' | 'en') => {
    setTemplateLanguage(lang);
    const template = researchTemplates[selectedTemplate as keyof typeof researchTemplates];
    if (template) {
      const templateText = lang === 'zh' ? template.template_zh : template.template_en;
      setCustomQuery(templateText);
    }
  };

  // Dropdown and delete state
  const [openDropdown, setOpenDropdown] = useState<string | null>(null);
  const [showDeleteConfirm, setShowDeleteConfirm] = useState<string | null>(null);
  const [showRegenerateModal, setShowRegenerateModal] = useState<Research | null>(null);
  const [regenerateResearchName, setRegenerateResearchName] = useState('');
  const [regenerateCustomQuery, setRegenerateCustomQuery] = useState('');

  // Duplicate management state
  const [showMergeModal, setShowMergeModal] = useState<string | null>(null);
  const [selectedResearchItems, setSelectedResearchItems] = useState<Set<string>>(new Set());

  // ============== Folder Management State ==============
  const [folderStructure, setFolderStructure] = useState<FolderStructure | null>(null);
  const [currentFolderId, setCurrentFolderId] = useState<string | null>(null);
  const [folderPath, setFolderPath] = useState<Array<{ id: string | null; name: string }>>([]);
  const [showCreateFolderModal, setShowCreateFolderModal] = useState(false);
  const [showMoveDocumentModal, setShowMoveDocumentModal] = useState(false);
  const [documentToMove, setDocumentToMove] = useState<string | null>(null);

  // Selection state for multi-select functionality
  const [selectedCards, setSelectedCards] = useState<Set<string>>(new Set());
  const [isSelectionMode, setIsSelectionMode] = useState(false);
  const [lastSelectedCard, setLastSelectedCard] = useState<string | null>(null);

  // Context menu state
  const [contextMenuPosition, setContextMenuPosition] = useState<{ x: number; y: number } | null>(null);
  const [contextMenuTarget, setContextMenuTarget] = useState<string | null>(null);

  // Drag-drop state
  const [dragOverFolderId, setDragOverFolderId] = useState<string | null>(null);

  // Lasso selection
  const cardGridRef = useRef<HTMLDivElement>(null);
  const cardRefs = useRef<Map<string, HTMLElement>>(new Map());

  const registerCardRef = useCallback((id: string, element: HTMLElement | null) => {
    if (element) {
      cardRefs.current.set(id, element);
    } else {
      cardRefs.current.delete(id);
    }
  }, []);

  // Get generation jobs from store (legacy, keep for compatibility)
  const { jobs, addJob, updateJob, removeJob } = useGenerationStore();

  // Monitor global tasks for doc_gen tasks in current repo
  const handleTaskComplete = useCallback(async (task: { task_id: string; repo_name: string; task_type: string }) => {
    // Only refresh if it's a doc_gen task for the current repo
    if (task.task_type === 'doc_gen' && task.repo_name === repoName) {
      console.log('[handleTaskComplete] Doc gen task completed, refreshing research list...');
      try {
        const response = await apiFetch(`/api/docs/operators/${repoName}`);
        const data = await response.json();
        setResearchList(data.operators || []);
      } catch (error) {
        console.error('Error refreshing research list:', error);
      }
    }
  }, [repoName]);

  // Use global tasks hook to monitor task completion and get active tasks
  const { tasks } = useGlobalTasks({
    repoName: repoName,
    onTaskComplete: handleTaskComplete,
    pollInterval: 5000,
    autoStart: true,
    stopWhenInactive: false,
  });

  // Filter for research-related tasks (doc_gen for current repo)
  const researchTasks = tasks.filter(task =>
    task.task_type === 'doc_gen' &&
    task.repo_name === repoName &&
    (task.status === 'pending' || task.status === 'running')
  );

  // Helper function to extract task name from status_message
  const getTaskName = (task: { status_message?: string; repo_name: string }) => {
    if (task.status_message && task.status_message.includes(':')) {
      return task.status_message.split(':')[0].trim();
    }
    return task.repo_name;
  };

  // Helper function to extract task step/message (without the name prefix)
  const getTaskStep = (task: { status_message?: string; step?: string }) => {
    if (task.status_message && task.status_message.includes(':')) {
      return task.status_message.split(':').slice(1).join(':').trim();
    }
    return task.step || task.status_message || '';
  };

  // Legacy researchJobs for backward compatibility
  const researchJobs = jobs.filter(job => job.type === 'research' && job.repoName === repoName);

  // Load research list on mount
  useEffect(() => {
    if (repoName) {
      loadResearchList();
    }
  }, [repoName]);

  // Close dropdown when clicking outside
  useEffect(() => {
    function handleClickOutside() {
      if (openDropdown) {
        setOpenDropdown(null);
      }
    }

    document.addEventListener('click', handleClickOutside);
    return () => document.removeEventListener('click', handleClickOutside);
  }, [openDropdown]);

  async function loadResearchList() {
    try {
      const response = await apiFetch(`/api/docs/operators/${repoName}`);
      const data = await response.json();
      setResearchList(data.operators || []);
    } catch (error) {
      console.error('Error loading research list:', error);
    } finally {
      setLoading(false);
    }
  }

  // ============== Folder Management Functions ==============

  const loadFolderStructure = useCallback(async () => {
    try {
      const client = getApiClient();
      const structure = await client.getFolderStructure(repoName);
      setFolderStructure(structure);
    } catch (error) {
      console.error('Error loading folder structure:', error);
    }
  }, [repoName]);

  const loadFolderPath = useCallback(async (folderId: string | null) => {
    if (!folderId) {
      setFolderPath([]);
      return;
    }
    try {
      const client = getApiClient();
      const path = await client.getFolderPath(repoName, folderId);
      setFolderPath(path.map((f: any) => ({ id: f.id, name: f.name })));
    } catch (error) {
      console.error('Error loading folder path:', error);
      setFolderPath([]);
    }
  }, [repoName]);

  const handleNavigateToFolder = useCallback((folderId: string | null) => {
    setCurrentFolderId(folderId);
    loadFolderPath(folderId);
    // Clear selection when navigating
    setSelectedCards(new Set());
    setIsSelectionMode(false);
  }, [loadFolderPath]);

  const handleCreateFolder = useCallback(async (name: string): Promise<string | null> => {
    try {
      const client = getApiClient();
      const newFolder = await client.createFolder(repoName, name, currentFolderId);
      await loadFolderStructure();
      setShowCreateFolderModal(false);
      showToast('success', `Folder "${name}" created`);
      return newFolder.id;
    } catch (error: any) {
      showToast('error', error.message || 'Failed to create folder');
      return null;
    }
  }, [repoName, currentFolderId, loadFolderStructure, showToast]);

  const handleDeleteFolder = useCallback(async (folderId: string) => {
    try {
      const client = getApiClient();
      await client.deleteFolder(repoName, folderId);
      await loadFolderStructure();
      showToast('success', 'Folder deleted');
    } catch (error: any) {
      showToast('error', error.message || 'Failed to delete folder');
    }
  }, [repoName, loadFolderStructure, showToast]);

  const handleMoveDocument = useCallback(async (docName: string, folderId: string | null) => {
    try {
      const client = getApiClient();
      await client.moveDocument(repoName, docName, folderId);
      await loadFolderStructure();
      setShowMoveDocumentModal(false);
      setDocumentToMove(null);
      showToast('success', 'Document moved');
    } catch (error: any) {
      showToast('error', error.message || 'Failed to move document');
    }
  }, [repoName, loadFolderStructure, showToast]);

  const handleBatchMoveDocuments = useCallback(async (folderId: string | null) => {
    if (selectedCards.size === 0) return;

    try {
      const client = getApiClient();
      const docNames = Array.from(selectedCards);
      await client.batchMoveDocuments(repoName, docNames, folderId);
      await loadFolderStructure();
      setSelectedCards(new Set());
      setIsSelectionMode(false);
      showToast('success', `Moved ${docNames.length} items`);
    } catch (error: any) {
      showToast('error', error.message || 'Failed to move documents');
    }
  }, [repoName, selectedCards, loadFolderStructure, showToast]);

  const handleCreateFolderFromSelection = useCallback(async () => {
    // This will open the create folder modal, then move selected items there
    setShowCreateFolderModal(true);
  }, []);

  const handleDeleteSelected = useCallback(async () => {
    if (selectedCards.size === 0) return;

    const confirmed = window.confirm(`Are you sure you want to delete ${selectedCards.size} item(s)?`);
    if (!confirmed) return;

    try {
      for (const name of selectedCards) {
        await fetch(`/api/repos/${repoName}/${name}`, { method: 'DELETE' });
      }
      await loadResearchList();
      await loadFolderStructure();
      setSelectedCards(new Set());
      setIsSelectionMode(false);
      showToast('success', `Deleted ${selectedCards.size} items`);
    } catch (error: any) {
      showToast('error', error.message || 'Failed to delete items');
    }
  }, [repoName, selectedCards, showToast]);

  // ============== Selection Functions ==============

  const handleCardSelect = useCallback((cardId: string, e: React.MouseEvent) => {
    e.preventDefault();
    e.stopPropagation();

    setSelectedCards(prev => {
      const newSet = new Set(prev);
      if (e.ctrlKey || e.metaKey) {
        // Toggle selection with Ctrl/Cmd
        if (newSet.has(cardId)) {
          newSet.delete(cardId);
        } else {
          newSet.add(cardId);
        }
      } else if (e.shiftKey && lastSelectedCard) {
        // Range selection with Shift
        const allItems = filteredResearchInCurrentFolder.map(r => r.name);
        const fromIndex = allItems.indexOf(lastSelectedCard);
        const toIndex = allItems.indexOf(cardId);
        if (fromIndex !== -1 && toIndex !== -1) {
          const start = Math.min(fromIndex, toIndex);
          const end = Math.max(fromIndex, toIndex);
          for (let i = start; i <= end; i++) {
            newSet.add(allItems[i]);
          }
        }
      } else {
        // Single selection
        if (newSet.has(cardId) && newSet.size === 1) {
          newSet.clear();
        } else {
          newSet.clear();
          newSet.add(cardId);
        }
      }

      setIsSelectionMode(newSet.size > 0);
      return newSet;
    });
    setLastSelectedCard(cardId);
  }, [lastSelectedCard]);

  const handleCardContextMenu = useCallback((cardId: string, e: React.MouseEvent) => {
    e.preventDefault();

    // If the card is not selected, select it first
    if (!selectedCards.has(cardId)) {
      setSelectedCards(new Set([cardId]));
    }

    setContextMenuTarget(cardId);
    setContextMenuPosition({ x: e.clientX, y: e.clientY });
  }, [selectedCards]);

  const handleCloseContextMenu = useCallback(() => {
    setContextMenuPosition(null);
    setContextMenuTarget(null);
  }, []);

  const handleClearSelection = useCallback(() => {
    setSelectedCards(new Set());
    setIsSelectionMode(false);
    setLastSelectedCard(null);
  }, []);

  // Keyboard shortcuts
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      // Escape to clear selection
      if (e.key === 'Escape') {
        handleClearSelection();
        handleCloseContextMenu();
      }
      // Ctrl+A to select all
      if ((e.ctrlKey || e.metaKey) && e.key === 'a') {
        e.preventDefault();
        const allNames = filteredResearchInCurrentFolder.map(r => r.name);
        setSelectedCards(new Set(allNames));
        setIsSelectionMode(true);
      }
    };

    document.addEventListener('keydown', handleKeyDown);
    return () => document.removeEventListener('keydown', handleKeyDown);
  }, [handleClearSelection, handleCloseContextMenu]);

  // Handle lasso selection
  const handleLassoSelection = useCallback((ids: Set<string>) => {
    setSelectedCards(ids);
    setIsSelectionMode(ids.size > 0);
  }, []);

  // Load folder structure on mount
  useEffect(() => {
    if (repoName) {
      loadFolderStructure();
    }
  }, [repoName, loadFolderStructure]);

  // Get folders in current directory
  const foldersInCurrentDir = folderStructure?.folders.filter(
    f => f.parentId === currentFolderId
  ) || [];

  // Metadata files to exclude from display
  const METADATA_FILES = ['_folders', '_meta', '_index', 'overview'];

  // Get documents in current folder
  const filteredResearchInCurrentFolder = researchList.filter((item) => {
    // Exclude metadata files
    if (METADATA_FILES.includes(item.name)) {
      return false;
    }
    // If no folder structure exists, show all documents at root level (currentFolderId === null)
    const docFolderId = folderStructure?.documentFolders?.[item.name];
    const inFolder = docFolderId === currentFolderId || (docFolderId === undefined && currentFolderId === null);
    const matchesSearch = item.name.toLowerCase().includes(searchQuery.toLowerCase());
    return inFolder && matchesSearch;
  });

  // Count documents in a folder (including subfolders)
  const getDocumentCountInFolder = useCallback((folderId: string): number => {
    if (!folderStructure) return 0;

    // Count direct documents
    let count = Object.entries(folderStructure.documentFolders)
      .filter(([_, docFolderId]) => docFolderId === folderId)
      .length;

    // Count in subfolders
    const subfolders = folderStructure.folders.filter(f => f.parentId === folderId);
    for (const subfolder of subfolders) {
      count += getDocumentCountInFolder(subfolder.id);
    }

    return count;
  }, [folderStructure]);

  // Get current folder name for breadcrumb
  const getCurrentFolderName = useCallback((): string | undefined => {
    if (!currentFolderId || !folderStructure) return undefined;
    return folderStructure.folders.find(f => f.id === currentFolderId)?.name;
  }, [currentFolderId, folderStructure]);

  async function handleAddResearch() {
    if (!researchName.trim()) {
      setAddError("Please enter document title");
      showToast("error", "Please enter document title");
      return;
    }

    if (!customQuery.trim()) {
      setAddError("Please enter research description");
      showToast("error", "Please enter research description");
      return;
    }

    setIsAdding(true);
    setAddError('');

    try {
      const response = await apiFetch('/api/docs/generate/async', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          repo: repoName,
          operator: researchName.trim(),
          query_template: customQuery.trim() || undefined,
          model: selectedModel || undefined,
        }),
      });

      const data = await response.json();

      if (!response.ok) {
        throw new Error(data.detail || data.message || 'Failed to start research');
      }

      if (!data.success) {
        throw new Error(data.message || 'Failed to start research');
      }

      // Add job to store
      addJob({
        id: data.task_id,
        type: 'research',
        name: researchName.trim(),
        repoName,
        status: 'pending',
        progress: 0,
        logs: [],
      });

      // Trigger task refresh to show the new task in TaskStatusPanel
      triggerTaskRefresh();

      showToast('success', 'Research task started, generating documentation...');

      // Close modal and reset state
      setShowAddResearchModal(false);
      setResearchName('');
      setCustomQuery('');
      setSelectedTemplate('operator');
      setSelectedModel('');

    } catch (error: any) {
      setAddError(error.message || 'Failed to start research task');
      showToast('error', error.message || 'Failed to start research task');
    } finally {
      setIsAdding(false);
    }
  }

  async function handleRegenerateResearch() {
    if (!showRegenerateModal) return;

    try {
      const response = await apiFetch('/api/docs/generate/async', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          repo: repoName,
          operator: regenerateResearchName || showRegenerateModal.name,
          query_template: regenerateCustomQuery.trim() || undefined,
        }),
      });

      const data = await response.json();

      if (!response.ok) {
        throw new Error(data.detail || data.message || 'Regeneration failed');
      }

      if (!data.success) {
        throw new Error(data.message || 'Regeneration failed');
      }

      addJob({
        id: data.task_id,
        type: 'research',
        name: regenerateResearchName || showRegenerateModal.name,
        repoName,
        status: 'pending',
        progress: 0,
        logs: [],
      });

      // Trigger task refresh to show the new task in TaskStatusPanel
      triggerTaskRefresh();

      showToast('success', 'Started regenerating documentation...');
      setShowRegenerateModal(null);
      setRegenerateResearchName('');
      setRegenerateCustomQuery('');
    } catch (error: any) {
      showToast('error', error.message || 'Regeneration failed');
    }
  }

  async function handleDeleteResearch(name: string) {
    try {
      const response = await fetch(`/api/repos/${repoName}/${name}`, {
        method: 'DELETE',
      });

      if (!response.ok) {
        throw new Error('Delete failed');
      }

      showToast('success', 'Deleted successfully');
      await loadResearchList();
      setShowDeleteConfirm(null);
    } catch (error: any) {
      showToast('error', error.message || 'Delete failed');
    }
  }

  function handleDeleteJob(jobId: string) {
    removeJob(jobId);
    setShowDeleteConfirm(null);
  }

  // Group research items by name to detect duplicates
  function groupResearchItems(items: Research[]): ResearchGroup[] {
    const groups = new Map<string, Research[]>();

    items.forEach(item => {
      if (!groups.has(item.name)) {
        groups.set(item.name, []);
      }
      groups.get(item.name)!.push(item);
    });

    return Array.from(groups.entries()).map(([name, groupItems]) => ({
      name,
      items: groupItems.sort((a, b) => new Date(b.lastUpdated).getTime() - new Date(a.lastUpdated).getTime()),
      isDuplicate: groupItems.length > 1
    }));
  }

  async function handleMergeResearchItems(name: string) {
    const group = groupResearchItems(researchList).find(g => g.name === name);
    if (!group || !group.isDuplicate) return;

    try {
      // Get the selected item (the one to keep)
      const selectedTimestamp = Array.from(selectedResearchItems)[0];
      if (!selectedTimestamp) {
        alert('Please select a document to keep');
        return;
      }

      const itemToKeep = group.items.find(item => item.lastUpdated === selectedTimestamp);
      if (!itemToKeep) return;

      // Delete all other versions
      for (const item of group.items) {
        if (item.lastUpdated !== selectedTimestamp) {
          try {
            await fetch(`/api/repos/${repoName}/${item.name}?timestamp=${item.lastUpdated}`, {
              method: 'DELETE',
            });
          } catch (error) {
            console.error('Error deleting duplicate:', error);
          }
        }
      }

      // Reload research list
      await loadResearchList();
      setShowMergeModal(null);
      setSelectedResearchItems(new Set());
    } catch (error) {
      console.error('Error merging research items:', error);
      alert('Merge failed');
    }
  }

  const filteredResearchList = researchList.filter((item) =>
    item.name.toLowerCase().includes(searchQuery.toLowerCase())
  );

  const researchGroups = groupResearchItems(filteredResearchList);

  if (loading) {
    return (
      <div style={{
        color: colors.text,
        height: '100%',
        overflow: 'auto',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center'
      }}>
        <div style={{ textAlign: 'center' }}>
          <div style={{
            width: '48px',
            height: '48px',
            border: `3px solid ${colors.border}`,
            borderTopColor: colors.accent,
            borderRadius: '50%',
            animation: 'spin 1s linear infinite',
            margin: '0 auto 16px'
          }} />
          <p style={{ color: colors.textMuted }}>Loading research...</p>
        </div>
        <style jsx>{`
          @keyframes spin {
            to { transform: rotate(360deg); }
          }
        `}</style>
      </div>
    );
  }

  return (
    <div style={{
      color: colors.text,
      height: '100%',
      overflow: 'auto',
    }}>
      {/* Page Title */}
      <section style={{ textAlign: 'center', marginTop: '48px', marginBottom: '24px' }}>
        <h2 style={{
          fontSize: '28px',
          fontWeight: '600',
          background: 'linear-gradient(135deg, #667eea 0%, #764ba2 100%)',
          WebkitBackgroundClip: 'text',
          WebkitTextFillColor: 'transparent',
          backgroundClip: 'text'
        }}>
          Select research document to view
        </h2>
      </section>

      {/* Folder Breadcrumb & Action Bar */}
      <div style={{
        display: 'flex',
        justifyContent: 'space-between',
        alignItems: 'center',
        padding: '0 48px',
        marginBottom: '24px',
        maxWidth: '1400px',
        margin: '0 auto 24px',
      }}>
        {/* Breadcrumb Navigation */}
        <FolderBreadcrumb
          path={[
            { id: null, name: repoName },
            ...folderPath
          ]}
          onNavigate={handleNavigateToFolder}
          theme={theme}
        />

        {/* Action Buttons */}
        <div style={{ display: 'flex', gap: '8px' }}>
          <button
            onClick={() => setShowCreateFolderModal(true)}
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: '6px',
              padding: '8px 14px',
              background: colors.buttonSecondaryBg,
              border: `1px solid ${colors.border}`,
              borderRadius: '8px',
              color: colors.text,
              fontSize: '13px',
              fontWeight: '500',
              cursor: 'pointer',
              transition: 'all 0.2s',
            }}
            onMouseEnter={(e) => {
              e.currentTarget.style.background = colors.buttonSecondaryHover;
              e.currentTarget.style.borderColor = colors.borderHover;
            }}
            onMouseLeave={(e) => {
              e.currentTarget.style.background = colors.buttonSecondaryBg;
              e.currentTarget.style.borderColor = colors.border;
            }}
          >
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"/>
              <line x1="12" y1="11" x2="12" y2="17"/>
              <line x1="9" y1="14" x2="15" y2="14"/>
            </svg>
            New Folder
          </button>
        </div>
      </div>

      {/* Search Box */}
      <div style={{ display: 'flex', justifyContent: 'center', marginBottom: '48px' }}>
        <div style={{ position: 'relative', width: '100%', maxWidth: '672px', padding: '0 24px' }}>
          <input
            type="text"
            placeholder="Search research documents"
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            style={{
              width: '100%',
              padding: '14px 48px 14px 20px',
              background: colors.inputBg,
              border: `2px solid ${colors.inputBorder}`,
              borderRadius: '12px',
              color: colors.text,
              fontSize: '14px',
              outline: 'none',
              transition: 'all 0.2s'
            }}
            onFocus={(e) => {
              e.currentTarget.style.borderColor = colors.accent;
              e.currentTarget.style.boxShadow = `0 0 0 3px ${colors.accentBg}`;
            }}
            onBlur={(e) => {
              e.currentTarget.style.borderColor = colors.inputBorder;
              e.currentTarget.style.boxShadow = 'none';
            }}
          />
          <span style={{
            position: 'absolute',
            right: '40px',
            top: '50%',
            transform: 'translateY(-50%)',
            opacity: 0.5,
            color: colors.textMuted,
          }}>
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <circle cx="11" cy="11" r="8"/>
              <line x1="21" y1="21" x2="16.65" y2="16.65"/>
            </svg>
          </span>
        </div>
      </div>

      {/* Card Grid */}
      <main
        ref={cardGridRef}
        style={{
          display: 'grid',
          gridTemplateColumns: 'repeat(auto-fill, minmax(280px, 1fr))',
          gap: '24px',
          padding: '0 48px 80px',
          maxWidth: '1400px',
          margin: '0 auto',
          position: 'relative',
        }}
      >
        {/* Lasso Selector */}
        <LassoSelector
          containerRef={cardGridRef}
          cardRefs={cardRefs.current}
          onSelectionChange={handleLassoSelection}
          enabled={true}
          theme={theme}
        />

        {/* Add Research Card */}
        <Card
          type="add"
          label="Add Research"
          onClick={() => setShowAddResearchModal(true)}
          theme={theme}
        />

        {/* Folder Cards */}
        {foldersInCurrentDir.map((folder) => (
          <div key={folder.id} style={{ position: 'relative' }}>
            <Card
              type="folder"
              name={folder.name}
              documentCount={getDocumentCountInFolder(folder.id)}
              lastUpdated={folder.updatedAt}
              onClick={() => handleNavigateToFolder(folder.id)}
              onMenuClick={(e) => {
                e.stopPropagation();
                setOpenDropdown(openDropdown === `folder-${folder.id}` ? null : `folder-${folder.id}`);
              }}
              showMenu={openDropdown === `folder-${folder.id}`}
              theme={theme}
              isSelectable={isSelectionMode}
              isSelected={selectedCards.has(`folder:${folder.id}`)}
              onSelect={(e) => handleCardSelect(`folder:${folder.id}`, e)}
              onContextMenu={(e) => handleCardContextMenu(folder.id, e)}
              isDragOver={dragOverFolderId === folder.id}
              onDragOver={(e) => {
                e.preventDefault();
                setDragOverFolderId(folder.id);
              }}
              onDragLeave={() => setDragOverFolderId(null)}
              onDrop={(e) => {
                e.preventDefault();
                setDragOverFolderId(null);
                handleBatchMoveDocuments(folder.id);
              }}
              cardRef={(el) => registerCardRef(`folder:${folder.id}`, el)}
              cardId={`folder:${folder.id}`}
            />

            <DropdownMenu
              isOpen={openDropdown === `folder-${folder.id}`}
              onClose={() => setOpenDropdown(null)}
              onDelete={() => handleDeleteFolder(folder.id)}
              theme={theme}
              variant="folder"
            />
          </div>
        ))}

        {/* Generating Research Cards - Using global task system as source of truth */}
        {researchTasks
          .filter(task => !filteredResearchInCurrentFolder.some(item => item.name === getTaskName(task)))
          .map((task) => (
          <Card
            key={task.task_id}
            type="job"
            name={getTaskName(task)}
            status={task.status === 'cancelled' ? 'failed' : task.status}
            progress={task.progress}
            currentStep={getTaskStep(task)}
            onClick={() => router.push(`/progress/${task.task_id}`)}
            theme={theme}
          />
        ))}

        {/* Research Cards */}
        {filteredResearchInCurrentFolder.map((item) => (
          <div key={item.name} style={{ position: 'relative' }}>
            <Card
              type="operator"
              name={item.name}
              lastUpdated={item.lastUpdated}
              referencesCount={item.metadata?.referencesCount}
              codeBlocksCount={item.metadata?.codeBlocksCount}
              hasDoc={true}
              onClick={() => !isSelectionMode && router.push(`/repos/${repoName}/${item.name}`)}
              onDoubleClick={() => router.push(`/repos/${repoName}/${item.name}`)}
              onMenuClick={(e) => {
                e.stopPropagation();
                setOpenDropdown(openDropdown === item.name ? null : item.name);
              }}
              showMenu={openDropdown === item.name}
              theme={theme}
              isSelectable={true}
              isSelected={selectedCards.has(item.name)}
              onSelect={(e) => handleCardSelect(item.name, e)}
              onContextMenu={(e) => handleCardContextMenu(item.name, e)}
              draggable={selectedCards.size > 0 && selectedCards.has(item.name)}
              onDragStart={(e) => {
                e.dataTransfer.setData('text/plain', Array.from(selectedCards).join(','));
                e.dataTransfer.effectAllowed = 'move';
              }}
              cardRef={(el) => registerCardRef(item.name, el)}
              cardId={item.name}
            />

            <DropdownMenu
              isOpen={openDropdown === item.name}
              onClose={() => setOpenDropdown(null)}
              onDelete={() => setShowDeleteConfirm(item.name)}
              onRegenerate={() => {
                setShowRegenerateModal(item);
                setRegenerateResearchName(item.name);
                setRegenerateCustomQuery(item.metadata?.query || '');
              }}
              onMoveToFolder={() => {
                setDocumentToMove(item.name);
                setShowMoveDocumentModal(true);
              }}
              theme={theme}
              variant="operator"
            />
          </div>
        ))}

        {filteredResearchInCurrentFolder.length === 0 && foldersInCurrentDir.length === 0 && researchList.length === 0 && !searchQuery && (
          <div style={{
            gridColumn: '1 / -1',
            textAlign: 'center',
            padding: '64px 24px'
          }}>
            <div style={{
              width: '64px',
              height: '64px',
              margin: '0 auto 16px',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              background: colors.accentBg,
              borderRadius: '16px',
            }}>
              <svg width="32" height="32" viewBox="0 0 24 24" fill="none" stroke={colors.accent} strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
                <path d="M9 3H5a2 2 0 0 0-2 2v4m6-6h10a2 2 0 0 1 2 2v4M9 3v18m0 0h10a2 2 0 0 0 2-2v-4M9 21H5a2 2 0 0 1-2-2v-4"/>
                <line x1="3" y1="9" x2="9" y2="9"/>
                <line x1="15" y1="9" x2="21" y2="9"/>
                <line x1="3" y1="15" x2="9" y2="15"/>
                <line x1="15" y1="15" x2="21" y2="15"/>
              </svg>
            </div>
            <h3 style={{ fontSize: '20px', fontWeight: '600', marginBottom: '8px', color: colors.text }}>
              No research documents
            </h3>
            <p style={{ fontSize: '14px', color: colors.textMuted, marginBottom: '24px' }}>
              Add your first research to start generating smart documentation
            </p>
          </div>
        )}

        {filteredResearchInCurrentFolder.length === 0 && foldersInCurrentDir.length === 0 && searchQuery && (
          <div style={{
            gridColumn: '1 / -1',
            textAlign: 'center',
            padding: '48px',
            color: colors.textMuted
          }}>
            <div style={{
              width: '48px',
              height: '48px',
              margin: '0 auto 16px',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
            }}>
              <svg width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
                <circle cx="11" cy="11" r="8"/>
                <line x1="21" y1="21" x2="16.65" y2="16.65"/>
              </svg>
            </div>
            <p style={{ fontSize: '16px' }}>No research documents found matching &quot;{searchQuery}&quot;</p>
          </div>
        )}
      </main>

      {/* Context Menu */}
      {contextMenuPosition && (
        <ContextMenu
          isOpen={true}
          position={contextMenuPosition}
          items={[
            {
              id: 'move-to-folder',
              label: 'Move to folder',
              icon: ContextMenuIcons.move,
            },
            {
              id: 'create-folder-from-selection',
              label: 'Create folder from selection',
              icon: ContextMenuIcons.folderPlus,
              disabled: selectedCards.size < 1,
              onClick: handleCreateFolderFromSelection,
            },
            {
              id: 'delete',
              label: `Delete ${selectedCards.size > 1 ? `${selectedCards.size} items` : 'item'}`,
              icon: ContextMenuIcons.trash,
              danger: true,
              onClick: handleDeleteSelected,
            },
            {
              id: 'deselect',
              label: 'Clear selection',
              icon: ContextMenuIcons.deselect,
              onClick: handleClearSelection,
            },
          ]}
          folders={folderStructure?.folders || []}
          onClose={handleCloseContextMenu}
          onMoveToFolder={handleBatchMoveDocuments}
          theme={theme}
        />
      )}

      {/* Selection Toolbar */}
      <SelectionToolbar
        selectedCount={selectedCards.size}
        onMoveToFolder={() => setShowMoveDocumentModal(true)}
        onCreateFolderFromSelection={handleCreateFolderFromSelection}
        onDelete={handleDeleteSelected}
        onClearSelection={handleClearSelection}
        theme={theme}
      />

      {/* Create Folder Modal */}
      <CreateFolderModal
        isOpen={showCreateFolderModal}
        onClose={() => setShowCreateFolderModal(false)}
        onConfirm={async (name) => {
          const newFolderId = await handleCreateFolder(name);
          // If items were selected and folder was created, move them to the new folder
          if (selectedCards.size > 0 && newFolderId) {
            await handleBatchMoveDocuments(newFolderId);
          }
        }}
        theme={theme}
      />

      {/* Move Document Modal */}
      <MoveDocumentModal
        isOpen={showMoveDocumentModal}
        onClose={() => {
          setShowMoveDocumentModal(false);
          setDocumentToMove(null);
        }}
        onConfirm={async (folderId) => {
          if (documentToMove) {
            await handleMoveDocument(documentToMove, folderId);
          } else if (selectedCards.size > 0) {
            await handleBatchMoveDocuments(folderId);
          }
        }}
        folders={folderStructure?.folders || []}
        currentFolderId={currentFolderId}
        documentName={documentToMove || (selectedCards.size > 0 ? `${selectedCards.size} items` : '')}
        theme={theme}
      />

      {/* Add Research Modal */}
      <Modal
        isOpen={showAddResearchModal}
        onClose={() => {
          setShowAddResearchModal(false);
          setResearchName('');
          setCustomQuery('');
          setAddError('');
          setSelectedTemplate('operator');
          setSelectedModel('');
        }}
        title="Add Research Document"
      >
        <div>
          {/* Template Selection */}
          <label style={{
            display: 'block',
            marginBottom: '8px',
            fontSize: '14px',
            fontWeight: '500',
            color: colors.text
          }}>
            Select research template
          </label>
          <div style={{
            display: 'grid',
            gridTemplateColumns: 'repeat(2, 1fr)',
            gap: '8px',
            marginBottom: '16px'
          }}>
            {Object.values(researchTemplates).map((template) => (
              <button
                key={template.id}
                onClick={() => handleTemplateSelect(template.id)}
                disabled={isAdding}
                style={{
                  padding: '10px 12px',
                  background: selectedTemplate === template.id ? colors.accentBg : colors.inputBg,
                  border: `2px solid ${selectedTemplate === template.id ? colors.accent : colors.inputBorder}`,
                  borderRadius: '8px',
                  cursor: isAdding ? 'not-allowed' : 'pointer',
                  textAlign: 'left',
                  transition: 'all 0.2s',
                }}
              >
                <div style={{
                  fontSize: '13px',
                  fontWeight: '600',
                  color: selectedTemplate === template.id ? colors.accent : colors.text,
                  marginBottom: '2px'
                }}>
                  {template.name}
                </div>
                <div style={{
                  fontSize: '11px',
                  color: colors.textMuted,
                  lineHeight: '1.3'
                }}>
                  {template.description}
                </div>
              </button>
            ))}
          </div>

          {/* Language Toggle */}
          <div style={{
            display: 'flex',
            alignItems: 'center',
            gap: '8px',
            marginBottom: '16px'
          }}>
            <span style={{ fontSize: "13px", color: colors.textMuted }}>Template language:</span>
            <button
              onClick={() => handleLanguageChange('zh')}
              disabled={isAdding}
              style={{
                padding: '4px 12px',
                background: templateLanguage === 'zh' ? colors.accent : colors.buttonSecondaryBg,
                border: 'none',
                borderRadius: '4px',
                fontSize: '12px',
                fontWeight: '500',
                cursor: isAdding ? 'not-allowed' : 'pointer',
                color: templateLanguage === 'zh' ? '#ffffff' : colors.text,
              }}
            >
              中文
            </button>
            <button
              onClick={() => handleLanguageChange('en')}
              disabled={isAdding}
              style={{
                padding: '4px 12px',
                background: templateLanguage === 'en' ? colors.accent : colors.buttonSecondaryBg,
                border: 'none',
                borderRadius: '4px',
                fontSize: '12px',
                fontWeight: '500',
                cursor: isAdding ? 'not-allowed' : 'pointer',
                color: templateLanguage === 'en' ? '#ffffff' : colors.text,
              }}
            >
              English
            </button>
          </div>

          {/* Model Selection */}
          <div style={{
            display: 'flex',
            alignItems: 'center',
            gap: '8px',
            marginBottom: '16px'
          }}>
            <span style={{ fontSize: "13px", color: colors.textMuted }}>Model:</span>
            <ModelCombobox
              value={selectedModel}
              onChange={setSelectedModel}
              disabled={isAdding}
              theme={theme}
              showTiers={false}
              presets={availableModels}
              placeholder="Select or type model..."
              style={{ flex: 1 }}
            />
          </div>

          {/* Document Title */}
          <label style={{
            display: 'block',
            marginBottom: '8px',
            fontSize: '14px',
            fontWeight: '500',
            color: colors.text
          }}>
            Document title <span style={{ color: colors.error }}>*</span>
          </label>
          <input
            type="text"
            value={researchName}
            onChange={(e) => setResearchName(e.target.value)}
            placeholder="E.g.: flash_attention_impl or attention mechanism research"
            disabled={isAdding}
            style={{
              width: '100%',
              padding: '10px 12px',
              background: colors.inputBg,
              border: `1px solid ${colors.inputBorder}`,
              borderRadius: '8px',
              color: colors.text,
              fontSize: '14px',
              outline: 'none',
              marginBottom: '16px'
            }}
            onFocus={(e) => {
              e.currentTarget.style.borderColor = colors.accent;
            }}
            onBlur={(e) => {
              e.currentTarget.style.borderColor = colors.inputBorder;
            }}
          />

          {/* Research Query */}
          <label style={{
            display: 'block',
            marginBottom: '8px',
            fontSize: '14px',
            fontWeight: '500',
            color: colors.text
          }}>
            Research description <span style={{ color: colors.error }}>*</span>
          </label>
          <textarea
            value={customQuery}
            onChange={(e) => setCustomQuery(e.target.value)}
            placeholder="Modify based on template, add specific research requirements..."
            disabled={isAdding}
            rows={8}
            style={{
              width: '100%',
              padding: '10px 12px',
              background: colors.inputBg,
              border: `1px solid ${colors.inputBorder}`,
              borderRadius: '8px',
              color: colors.text,
              fontSize: '14px',
              outline: 'none',
              marginBottom: '16px',
              fontFamily: 'inherit',
              resize: 'vertical',
              lineHeight: '1.5'
            }}
            onFocus={(e) => {
              e.currentTarget.style.borderColor = colors.accent;
            }}
            onBlur={(e) => {
              e.currentTarget.style.borderColor = colors.inputBorder;
            }}
          />

          {addError && (
            <div style={{
              padding: '12px',
              background: colors.errorBg,
              border: `1px solid ${colors.errorBorder}`,
              borderRadius: '8px',
              color: colors.error,
              fontSize: '14px',
              marginBottom: '16px'
            }}>
              {addError}
            </div>
          )}

          <div style={{
            fontSize: '13px',
            color: colors.textMuted,
            marginBottom: '20px',
            padding: '12px',
            background: colors.accentBg,
            borderRadius: '8px',
            border: `1px solid ${colors.accent}20`
          }}>
            <p style={{ marginBottom: '8px', display: 'flex', alignItems: 'center', gap: '6px' }}>
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <path d="M9 18h6"/>
                <path d="M10 22h4"/>
                <path d="M12 2a7 7 0 0 0-7 7c0 2.38 1.19 4.47 3 5.74V17a2 2 0 0 0 2 2h4a2 2 0 0 0 2-2v-2.26c1.81-1.27 3-3.36 3-5.74a7 7 0 0 0-7-7z"/>
              </svg>
              <strong>Tips</strong>
            </p>
            <ul style={{ marginLeft: '20px', marginTop: '0' }}>
              <li>Template will auto-fill research framework</li>
              <li>Fill in research objects in [ ]</li>
              <li>Modify and supplement research requirements freely</li>
              <li>Generation may take 1-3 minutes</li>
            </ul>
          </div>

          <div style={{ display: 'flex', gap: '12px', justifyContent: 'flex-end' }}>
            <button
              onClick={() => {
                setShowAddResearchModal(false);
                setResearchName('');
                setCustomQuery('');
                setAddError('');
                setSelectedTemplate('operator');
                setSelectedModel('');
              }}
              disabled={isAdding}
              style={{
                padding: '10px 20px',
                background: colors.buttonSecondaryBg,
                border: 'none',
                borderRadius: '8px',
                fontSize: '14px',
                fontWeight: '500',
                cursor: isAdding ? 'not-allowed' : 'pointer',
                color: colors.text,
                opacity: isAdding ? 0.5 : 1
              }}
            >
              取消
            </button>
            <button
              onClick={handleAddResearch}
              disabled={isAdding || !researchName.trim() || !customQuery.trim()}
              style={{
                padding: '10px 20px',
                background: (isAdding || !researchName.trim() || !customQuery.trim()) ? colors.textDimmed : colors.buttonPrimaryBg,
                border: 'none',
                borderRadius: '8px',
                fontSize: '14px',
                fontWeight: '500',
                cursor: (isAdding || !researchName.trim() || !customQuery.trim()) ? 'not-allowed' : 'pointer',
                color: '#ffffff',
                display: 'flex',
                alignItems: 'center',
                gap: '8px'
              }}
            >
              {isAdding && (
                <div style={{
                  width: '16px',
                  height: '16px',
                  border: '2px solid #ffffff',
                  borderTopColor: 'transparent',
                  borderRadius: '50%',
                  animation: 'spin 0.6s linear infinite'
                }} />
              )}
              {isAdding ? '生成中...' : '开始研究'}
            </button>
          </div>
        </div>
      </Modal>

      {/* Regenerate Research Modal */}
      {showRegenerateModal && (
        <Modal
          isOpen={true}
          onClose={() => {
            setShowRegenerateModal(null);
            setRegenerateResearchName('');
            setRegenerateCustomQuery('');
          }}
          title="Regenerate Document"
        >
          <div>
            <label style={{
              display: 'block',
              marginBottom: '8px',
              fontSize: '14px',
              fontWeight: '500',
              color: colors.text
            }}>
              Document name
            </label>
            <input
              type="text"
              value={regenerateResearchName}
              onChange={(e) => setRegenerateResearchName(e.target.value)}
              style={{
                width: '100%',
                padding: '10px 12px',
                background: colors.inputBg,
                border: `1px solid ${colors.inputBorder}`,
                borderRadius: '8px',
                color: colors.text,
                fontSize: '14px',
                outline: 'none',
                marginBottom: '16px'
              }}
            />

            <label style={{
              display: 'block',
              marginBottom: '8px',
              fontSize: '14px',
              fontWeight: '500',
              color: colors.text
            }}>
              Research description (optional)
            </label>
            <textarea
              value={regenerateCustomQuery}
              onChange={(e) => setRegenerateCustomQuery(e.target.value)}
              placeholder="Leave empty to use original description"
              rows={3}
              style={{
                width: '100%',
                padding: '10px 12px',
                background: colors.inputBg,
                border: `1px solid ${colors.inputBorder}`,
                borderRadius: '8px',
                color: colors.text,
                fontSize: '14px',
                outline: 'none',
                marginBottom: '20px',
                resize: 'vertical'
              }}
            />

            <div style={{ display: 'flex', gap: '12px', justifyContent: 'flex-end' }}>
              <button
                onClick={() => {
                  setShowRegenerateModal(null);
                  setRegenerateResearchName('');
                  setRegenerateCustomQuery('');
                }}
                style={{
                  padding: '10px 20px',
                  background: colors.buttonSecondaryBg,
                  border: 'none',
                  borderRadius: '8px',
                  fontSize: '14px',
                  fontWeight: '500',
                  cursor: 'pointer',
                  color: colors.text
                }}
              >
                取消
              </button>
              <button
                onClick={handleRegenerateResearch}
                style={{
                  padding: '10px 20px',
                  background: colors.buttonPrimaryBg,
                  border: 'none',
                  borderRadius: '8px',
                  fontSize: '14px',
                  fontWeight: '500',
                  cursor: 'pointer',
                  color: '#ffffff'
                }}
              >
                重新生成
              </button>
            </div>
          </div>
        </Modal>
      )}

      {/* Delete Confirmation Modal */}
      {showDeleteConfirm && (
        <Modal
          isOpen={true}
          onClose={() => setShowDeleteConfirm(null)}
          title="Confirm Delete"
        >
          <div>
            <p style={{
              marginBottom: '20px',
              color: colors.textMuted
            }}>
              Are you sure you want to delete research document <strong>{showDeleteConfirm}</strong>? This action cannot be undone.
            </p>
            <div style={{ display: 'flex', gap: '12px', justifyContent: 'flex-end' }}>
              <button
                onClick={() => setShowDeleteConfirm(null)}
                style={{
                  padding: '10px 20px',
                  background: colors.buttonSecondaryBg,
                  border: 'none',
                  borderRadius: '8px',
                  fontSize: '14px',
                  fontWeight: '500',
                  cursor: 'pointer',
                  color: colors.text
                }}
              >
                取消
              </button>
              <button
                onClick={() => handleDeleteResearch(showDeleteConfirm)}
                style={{
                  padding: '10px 20px',
                  background: colors.error,
                  border: 'none',
                  borderRadius: '8px',
                  fontSize: '14px',
                  fontWeight: '500',
                  cursor: 'pointer',
                  color: '#ffffff'
                }}
              >
                删除
              </button>
            </div>
          </div>
        </Modal>
      )}

      {/* Merge Duplicates Modal */}
      <Modal
        isOpen={!!showMergeModal}
        onClose={() => setShowMergeModal(null)}
        title="Merge Duplicate Documents"
      >
        <div>
          <p style={{ marginBottom: '20px', color: colors.text }}>
            您即将合并 &quot;{showMergeModal}&quot; 的多个版本。<br/>
            请选择您要保留的文档（最新时间戳的版本）。
          </p>
          <div style={{ display: 'flex', flexDirection: 'column', gap: '12px' }}>
            {groupResearchItems(researchList).find(g => g.name === showMergeModal)?.items.map((item) => (
              <div key={item.lastUpdated} style={{ display: 'flex', alignItems: 'center', gap: '12px' }}>
                <input
                  type="radio"
                  name="duplicateResearch"
                  value={item.lastUpdated}
                  checked={selectedResearchItems.has(item.lastUpdated)}
                  onChange={() => {
                    setSelectedResearchItems(prev => {
                      const newSet = new Set(prev);
                      if (newSet.has(item.lastUpdated)) {
                        newSet.delete(item.lastUpdated);
                      } else {
                        newSet.add(item.lastUpdated);
                      }
                      return newSet;
                    });
                  }}
                  style={{
                    width: '20px',
                    height: '20px',
                    accentColor: colors.accent
                  }}
                />
                <span style={{ fontSize: '14px', color: colors.text }}>
                  {item.name} (Updated: {new Date(item.lastUpdated).toLocaleDateString()})
                </span>
              </div>
            ))}
          </div>
          <div style={{ display: 'flex', gap: '12px', justifyContent: 'flex-end', marginTop: '20px' }}>
            <button
              onClick={() => setShowMergeModal(null)}
              style={{
                padding: '10px 20px',
                background: colors.buttonSecondaryBg,
                border: 'none',
                borderRadius: '8px',
                fontSize: '14px',
                fontWeight: '500',
                cursor: 'pointer',
                color: colors.text
              }}
            >
              取消
            </button>
            <button
              onClick={() => handleMergeResearchItems(showMergeModal!)}
              disabled={selectedResearchItems.size === 0}
              style={{
                padding: '10px 20px',
                background: colors.buttonPrimaryBg,
                border: 'none',
                borderRadius: '8px',
                fontSize: '14px',
                fontWeight: '500',
                cursor: selectedResearchItems.size === 0 ? 'not-allowed' : 'pointer',
                color: '#ffffff'
              }}
            >
              {selectedResearchItems.size === 0 ? "Please select a document to keep" : "Merge Duplicate Documents"}
            </button>
          </div>
        </div>
      </Modal>

      <style jsx>{`
        @keyframes spin {
          to { transform: rotate(360deg); }
        }
        @keyframes bounce-slow {
          0%, 100% { transform: translateY(0); }
          50% { transform: translateY(-10px); }
        }
      `}</style>
    </div>
  );
}
