/**
 * Page Editor Component - Mobile-friendly Markdown Editor
 * For creating and editing subject pages/lessons
 */

import { useState, useEffect } from 'react';
import { X, Save, Eye, Edit3, Bold, Italic, List, Link } from 'lucide-react';
import { toast } from 'sonner';
import axios from 'axios';

const API_BASE = import.meta.env.VITE_API_URL || 'http://localhost:3000';

export default function PageEditor({ page, subjectId, onSave, onCancel }) {
  const [title, setTitle] = useState('');
  const [content, setContent] = useState('');
  const [pageOrder, setPageOrder] = useState(1);
  const [loading, setLoading] = useState(false);
  const [previewMode, setPreviewMode] = useState(false);

  useEffect(() => {
    if (page) {
      setTitle(page.title || '');
      setContent(page.content || '');
      setPageOrder(page.page_order || 1);
    }
  }, [page]);

  const handleSave = async () => {
    if (!title.trim()) {
      toast.error('Please enter a title');
      return;
    }

    if (!content.trim()) {
      toast.error('Please enter some content');
      return;
    }

    if (!subjectId && !page?.subject_id) {
      toast.error('Subject ID is required');
      return;
    }

    setLoading(true);
    try {
      const token = localStorage.getItem('staffToken');
      const url = page?.id
        ? `${API_BASE}/api/staff/subject-pages/${page.id}`
        : `${API_BASE}/api/staff/subject-pages`;

      const method = page?.id ? 'put' : 'post';
      const payload = {
        subject_id: subjectId || page?.subject_id,
        title: title.trim(),
        content: content.trim(),
        page_order: pageOrder,
      };

      await axios[method](url, payload, {
        headers: { Authorization: `Bearer ${token}` },
      });

      toast.success(page?.id ? 'Page updated successfully' : 'Page created successfully');
      onSave();
    } catch (error) {
      console.error('Save page error:', error);
      toast.error('Failed to save page');
    } finally {
      setLoading(false);
    }
  };

  const insertMarkdown = (before, after = '') => {
    const textarea = document.getElementById('content-editor');
    const start = textarea.selectionStart;
    const end = textarea.selectionEnd;
    const selectedText = content.substring(start, end);
    
    const newText = 
      content.substring(0, start) + 
      before + 
      selectedText + 
      after + 
      content.substring(end);
    
    setContent(newText);
    
    // Set cursor position after insertion
    setTimeout(() => {
      textarea.focus();
      textarea.setSelectionRange(start + before.length, start + before.length + selectedText.length);
    }, 0);
  };

  const renderPreview = (markdown) => {
    // Simple markdown rendering (in production, use react-markdown)
    return markdown
      .replace(/^### (.*$)/gim, '<h3 class="text-lg font-bold mt-4 mb-2">$1</h3>')
      .replace(/^## (.*$)/gim, '<h2 class="text-xl font-bold mt-5 mb-3">$1</h2>')
      .replace(/^# (.*$)/gim, '<h1 class="text-2xl font-bold mt-6 mb-4">$1</h1>')
      .replace(/\*\*(.*)\*\*/gim, '<strong>$1</strong>')
      .replace(/\*(.*)\*/gim, '<em>$1</em>')
      .replace(/^- (.*)$/gim, '<li class="ml-4 list-disc">$1</li>')
      .replace(/\n/gim, '<br />');
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/50">
      <div className="bg-white rounded-xl shadow-2xl w-full max-w-4xl max-h-[90vh] overflow-hidden flex flex-col">
        {/* Header */}
        <div className="flex items-center justify-between p-4 border-b bg-gray-50">
          <h2 className="text-lg font-bold text-gray-900">
            {page?.id ? 'Edit Page' : 'Create New Page'}
          </h2>
          <button
            onClick={onCancel}
            className="p-2 hover:bg-gray-200 rounded-lg transition-colors"
          >
            <X size={20} className="text-gray-700" />
          </button>
        </div>

        {/* Toolbar */}
        <div className="flex items-center gap-2 p-3 border-b bg-white overflow-x-auto">
          <button
            onClick={() => insertMarkdown('**', '**')}
            className="p-2 hover:bg-gray-100 rounded"
            title="Bold"
          >
            <Bold size={18} />
          </button>
          <button
            onClick={() => insertMarkdown('*', '*')}
            className="p-2 hover:bg-gray-100 rounded"
            title="Italic"
          >
            <Italic size={18} />
          </button>
          <button
            onClick={() => insertMarkdown('- ')}
            className="p-2 hover:bg-gray-100 rounded"
            title="Bullet List"
          >
            <List size={18} />
          </button>
          <button
            onClick={() => insertMarkdown('[', '](url)')}
            className="p-2 hover:bg-gray-100 rounded"
            title="Link"
          >
            <Link size={18} />
          </button>
          <div className="border-l h-6 mx-2" />
          <button
            onClick={() => setPreviewMode(!previewMode)}
            className={`flex items-center gap-2 px-3 py-1.5 rounded transition-colors ${
              previewMode ? 'bg-purple-100 text-purple-700' : 'hover:bg-gray-100'
            }`}
          >
            {previewMode ? <Edit3 size={18} /> : <Eye size={18} />}
            <span className="text-sm">{previewMode ? 'Edit' : 'Preview'}</span>
          </button>
        </div>

        {/* Content Area */}
        <div className="flex-1 overflow-y-auto p-4">
          <div className="space-y-4">
            {/* Title Input */}
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">
                Page Title *
              </label>
              <input
                type="text"
                value={title}
                onChange={(e) => setTitle(e.target.value)}
                placeholder="Enter page title..."
                className="w-full px-4 py-2 border rounded-lg focus:outline-none focus:ring-2 focus:ring-purple-500"
                disabled={loading}
              />
            </div>

            {/* Page Order */}
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">
                Page Order
              </label>
              <input
                type="number"
                value={pageOrder}
                onChange={(e) => setPageOrder(parseInt(e.target.value) || 1)}
                min="1"
                className="w-24 px-4 py-2 border rounded-lg focus:outline-none focus:ring-2 focus:ring-purple-500"
                disabled={loading}
              />
            </div>

            {/* Content Editor / Preview */}
            <div className="flex-1 min-h-[300px]">
              <label className="block text-sm font-medium text-gray-700 mb-1">
                Content (Markdown) *
              </label>
              
              {previewMode ? (
                <div
                  className="prose prose-sm max-w-none border rounded-lg p-4 min-h-[300px] bg-gray-50"
                  dangerouslySetInnerHTML={{ __html: renderPreview(content) }}
                />
              ) : (
                <textarea
                  id="content-editor"
                  value={content}
                  onChange={(e) => setContent(e.target.value)}
                  placeholder="# Start writing your lesson content...

Use Markdown formatting:
- **bold** for bold text
- *italic* for italic text
- # Heading 1
- ## Heading 2
- ### Heading 3
- - Bullet points
- [Link text](url) for links"
                  className="w-full h-[400px] px-4 py-3 border rounded-lg focus:outline-none focus:ring-2 focus:ring-purple-500 font-mono text-sm resize-none"
                  disabled={loading}
                />
              )}
            </div>
          </div>
        </div>

        {/* Footer Actions */}
        <div className="flex items-center justify-end gap-3 p-4 border-t bg-gray-50">
          <button
            onClick={onCancel}
            className="px-6 py-2 text-gray-700 hover:bg-gray-200 rounded-lg transition-colors"
            disabled={loading}
          >
            Cancel
          </button>
          <button
            onClick={handleSave}
            disabled={loading}
            className="flex items-center gap-2 px-6 py-2 bg-purple-600 text-white rounded-lg hover:bg-purple-700 transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
          >
            <Save size={18} />
            {loading ? 'Saving...' : (page?.id ? 'Update Page' : 'Create Page')}
          </button>
        </div>
      </div>
    </div>
  );
}
