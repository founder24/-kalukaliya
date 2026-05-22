/**
 * Staff Dashboard - Main Content Hub Interface
 * Mobile-responsive CMS for educational content management
 */

import { useState, useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import { 
  Menu, X, BookOpen, FileText, Plus, Edit2, Trash2, 
  ChevronRight, ChevronDown, LogOut, Search, Filter 
} from 'lucide-react';
import { toast } from 'sonner';
import axios from 'axios';
import MobileSidebar from '../../components/staff/MobileSidebar';
import PageEditor from '../../components/staff/PageEditor';

const API_BASE = import.meta.env.VITE_API_URL || 'http://localhost:3000';

// Permission constants
const PERMISSIONS = {
  canEditSubjects: true,
  canDeleteSubjects: false, // Staff CANNOT delete subjects
  canEditClasses: false,    // Staff CANNOT edit classes
  canDeleteClasses: false,  // Staff CANNOT delete classes
  canEditBoards: false,     // Staff CANNOT edit boards
  canDeleteBoards: false,   // Staff CANNOT delete boards
  canCreatePages: true,
  canEditPages: true,
  canDeletePages: true,
};

export default function StaffDashboard() {
  const navigate = useNavigate();
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [loading, setLoading] = useState(true);
  const [contentData, setContentData] = useState({
    boards: [],
    classes: [],
    subjects: [],
    pages: [],
  });
  const [selectedBoard, setSelectedBoard] = useState(null);
  const [selectedClass, setSelectedClass] = useState(null);
  const [selectedSubject, setSelectedSubject] = useState(null);
  const [expandedBoards, setExpandedBoards] = useState({});
  const [expandedClasses, setExpandedClasses] = useState({});
  const [searchQuery, setSearchQuery] = useState('');
  const [showPageEditor, setShowPageEditor] = useState(false);
  const [editingPage, setEditingPage] = useState(null);

  useEffect(() => {
    checkAuth();
    fetchContentHub();
  }, []);

  const checkAuth = () => {
    const token = localStorage.getItem('staffToken');
    const role = localStorage.getItem('staffRole');
    
    if (!token || role !== 'staff') {
      toast.error('Please login as staff');
      navigate('/staff/login');
    }
  };

  const fetchContentHub = async () => {
    try {
      const token = localStorage.getItem('staffToken');
      const response = await axios.get(`${API_BASE}/api/staff/content-hub`, {
        headers: { Authorization: `Bearer ${token}` },
      });

      if (response.data) {
        setContentData(response.data);
      }
    } catch (error) {
      console.error('Failed to fetch content hub:', error);
      toast.error('Failed to load content');
    } finally {
      setLoading(false);
    }
  };

  const toggleBoard = (boardId) => {
    setExpandedBoards(prev => ({
      ...prev,
      [boardId]: !prev[boardId],
    }));
  };

  const toggleClass = (classId) => {
    setExpandedClasses(prev => ({
      ...prev,
      [classId]: !prev[classId],
    }));
  };

  const handleLogout = () => {
    localStorage.removeItem('staffToken');
    localStorage.removeItem('staffRole');
    toast.success('Logged out successfully');
    navigate('/staff/login');
  };

  const handleCreatePage = () => {
    if (!selectedSubject) {
      toast.error('Please select a subject first');
      return;
    }
    setEditingPage(null);
    setShowPageEditor(true);
  };

  const handleEditPage = (page) => {
    setEditingPage(page);
    setShowPageEditor(true);
  };

  const handleDeletePage = async (pageId) => {
    if (!window.confirm('Are you sure you want to delete this page?')) return;

    try {
      const token = localStorage.getItem('staffToken');
      await axios.delete(`${API_BASE}/api/staff/subject-pages/${pageId}`, {
        headers: { Authorization: `Bearer ${token}` },
      });
      toast.success('Page deleted successfully');
      fetchContentHub();
    } catch (error) {
      console.error('Delete page error:', error);
      toast.error('Failed to delete page');
    }
  };

  const filteredSubjects = contentData.subjects?.filter(subject => {
    if (!searchQuery) return true;
    return subject.name.toLowerCase().includes(searchQuery.toLowerCase());
  }) || [];

  if (loading) {
    return (
      <div className="min-h-screen bg-gray-50 flex items-center justify-center">
        <div className="text-center">
          <div className="animate-spin rounded-full h-12 w-12 border-b-2 border-purple-600 mx-auto"></div>
          <p className="mt-4 text-gray-600">Loading content hub...</p>
        </div>
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-gray-50">
      {/* Mobile Header */}
      <header className="bg-white shadow-sm border-b sticky top-0 z-30 lg:hidden">
        <div className="flex items-center justify-between px-4 py-3">
          <button
            onClick={() => setSidebarOpen(true)}
            className="p-2 rounded-lg hover:bg-gray-100"
          >
            <Menu size={24} className="text-gray-700" />
          </button>
          <h1 className="text-lg font-bold text-gray-900">Staff CMS</h1>
          <button
            onClick={handleLogout}
            className="p-2 rounded-lg hover:bg-gray-100"
          >
            <LogOut size={20} className="text-gray-700" />
          </button>
        </div>
      </header>

      {/* Mobile Sidebar Overlay */}
      {sidebarOpen && (
        <div
          className="fixed inset-0 bg-black/50 z-40 lg:hidden"
          onClick={() => setSidebarOpen(false)}
        />
      )}

      {/* Sidebar */}
      <MobileSidebar
        isOpen={sidebarOpen}
        onClose={() => setSidebarOpen(false)}
        boards={contentData.boards}
        classes={contentData.classes}
        subjects={contentData.subjects}
        expandedBoards={expandedBoards}
        expandedClasses={expandedClasses}
        toggleBoard={toggleBoard}
        toggleClass={toggleClass}
        selectedBoard={selectedBoard}
        selectedClass={selectedClass}
        selectedSubject={selectedSubject}
        setSelectedBoard={setSelectedBoard}
        setSelectedClass={setSelectedClass}
        setSelectedSubject={setSelectedSubject}
        permissions={PERMISSIONS}
      />

      {/* Desktop Layout */}
      <div className="flex">
        {/* Desktop Sidebar */}
        <aside className="hidden lg:block w-72 bg-white border-r min-h-screen fixed left-0 top-0 overflow-y-auto">
          <div className="p-4 border-b">
            <h1 className="text-xl font-bold text-purple-700">Syrabit.ai</h1>
            <p className="text-xs text-gray-500 mt-1">Staff Management Portal</p>
          </div>

          <div className="p-4">
            <div className="relative mb-4">
              <Search size={18} className="absolute left-3 top-1/2 transform -translate-y-1/2 text-gray-400" />
              <input
                type="text"
                placeholder="Search subjects..."
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
                className="w-full pl-10 pr-4 py-2 border rounded-lg focus:outline-none focus:ring-2 focus:ring-purple-500"
              />
            </div>

            {/* Boards & Classes Tree */}
            <div className="space-y-2">
              <h3 className="text-sm font-semibold text-gray-700 uppercase tracking-wider">Content Structure</h3>
              
              {contentData.boards?.map(board => (
                <div key={board.id} className="border rounded-lg">
                  <button
                    onClick={() => toggleBoard(board.id)}
                    className="w-full flex items-center justify-between p-3 hover:bg-gray-50 rounded-t-lg"
                    disabled={!PERMISSIONS.canEditBoards}
                  >
                    <div className="flex items-center gap-2">
                      <BookOpen size={18} className="text-blue-500" />
                      <span className="font-medium text-gray-900">{board.name}</span>
                    </div>
                    {expandedBoards[board.id] ? (
                      <ChevronDown size={16} className="text-gray-400" />
                    ) : (
                      <ChevronRight size={16} className="text-gray-400" />
                    )}
                  </button>

                  {expandedBoards[board.id] && (
                    <div className="pl-4 pb-2">
                      {contentData.classes
                        ?.filter(c => c.board_id === board.id)
                        .map(cls => (
                          <div key={cls.id}>
                            <button
                              onClick={() => toggleClass(cls.id)}
                              className="w-full flex items-center justify-between p-2 hover:bg-gray-50 rounded"
                              disabled={!PERMISSIONS.canEditClasses}
                            >
                              <span className="text-sm text-gray-700">{cls.name}</span>
                              {expandedClasses[cls.id] ? (
                                <ChevronDown size={14} className="text-gray-400" />
                              ) : (
                                <ChevronRight size={14} className="text-gray-400" />
                              )}
                            </button>

                            {expandedClasses[cls.id] && (
                              <div className="pl-4 mt-1 space-y-1">
                                {contentData.subjects
                                  ?.filter(s => s.class_id === cls.id)
                                  .map(subject => (
                                    <button
                                      key={subject.id}
                                      onClick={() => setSelectedSubject(subject)}
                                      className={`w-full text-left px-2 py-1.5 rounded text-sm ${
                                        selectedSubject?.id === subject.id
                                          ? 'bg-purple-100 text-purple-700'
                                          : 'hover:bg-gray-100 text-gray-600'
                                      }`}
                                    >
                                      {subject.name}
                                    </button>
                                  ))}
                              </div>
                            )}
                          </div>
                        ))}
                    </div>
                  )}
                </div>
              ))}
            </div>
          </div>

          <div className="p-4 border-t mt-auto">
            <button
              onClick={handleLogout}
              className="w-full flex items-center justify-center gap-2 px-4 py-2 text-red-600 hover:bg-red-50 rounded-lg transition-colors"
            >
              <LogOut size={18} />
              Logout
            </button>
          </div>
        </aside>

        {/* Main Content Area */}
        <main className="flex-1 lg:ml-72 p-4 lg:p-8">
          <div className="max-w-6xl mx-auto">
            {/* Header */}
            <div className="flex items-center justify-between mb-6">
              <div>
                <h2 className="text-2xl font-bold text-gray-900">
                  {selectedSubject ? selectedSubject.name : 'Select a Subject'}
                </h2>
                <p className="text-gray-500 text-sm mt-1">
                  {selectedSubject?.description || 'Manage educational content pages'}
                </p>
              </div>
              {selectedSubject && (
                <button
                  onClick={handleCreatePage}
                  className="flex items-center gap-2 px-4 py-2 bg-purple-600 text-white rounded-lg hover:bg-purple-700 transition-colors"
                >
                  <Plus size={18} />
                  <span className="hidden sm:inline">Add Page</span>
                </button>
              )}
            </div>

            {/* Content Pages Grid */}
            {selectedSubject ? (
              <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-3">
                {contentData.pages
                  ?.filter(p => p.subject_id === selectedSubject.id)
                  .map((page, index) => (
                    <div
                      key={page.id}
                      className="bg-white border rounded-lg p-4 hover:shadow-md transition-shadow"
                    >
                      <div className="flex items-start justify-between mb-2">
                        <h3 className="font-semibold text-gray-900 line-clamp-2">
                          {page.title}
                        </h3>
                        <span className="text-xs text-gray-400 bg-gray-100 px-2 py-1 rounded">
                          #{index + 1}
                        </span>
                      </div>
                      <p className="text-sm text-gray-600 line-clamp-3 mb-4">
                        {page.content.replace(/[#*_`]/g, '').slice(0, 100)}...
                      </p>
                      <div className="flex items-center gap-2">
                        <button
                          onClick={() => handleEditPage(page)}
                          className="flex-1 flex items-center justify-center gap-1 px-3 py-1.5 text-sm bg-blue-50 text-blue-600 rounded hover:bg-blue-100 transition-colors"
                        >
                          <Edit2 size={14} />
                          Edit
                        </button>
                        {PERMISSIONS.canDeletePages && (
                          <button
                            onClick={() => handleDeletePage(page.id)}
                            className="flex items-center justify-center gap-1 px-3 py-1.5 text-sm bg-red-50 text-red-600 rounded hover:bg-red-100 transition-colors"
                          >
                            <Trash2 size={14} />
                          </button>
                        )}
                      </div>
                    </div>
                  ))}

                {(!contentData.pages?.length || contentData.pages.filter(p => p.subject_id === selectedSubject.id).length === 0) && (
                  <div className="col-span-full text-center py-12 bg-white border rounded-lg">
                    <FileText size={48} className="mx-auto text-gray-300 mb-4" />
                    <p className="text-gray-500">No pages yet</p>
                    <button
                      onClick={handleCreatePage}
                      className="mt-4 text-purple-600 hover:text-purple-700 font-medium"
                    >
                      Create your first page →
                    </button>
                  </div>
                )}
              </div>
            ) : (
              <div className="bg-white border rounded-lg p-12 text-center">
                <BookOpen size={64} className="mx-auto text-gray-300 mb-4" />
                <h3 className="text-xl font-semibold text-gray-900 mb-2">
                  Select a Subject to Manage Content
                </h3>
                <p className="text-gray-500 max-w-md mx-auto">
                  Choose a subject from the sidebar to view and edit its content pages.
                  You can create new pages, edit existing ones, or organize the content structure.
                </p>
              </div>
            )}
          </div>
        </main>
      </div>

      {/* Page Editor Modal */}
      {showPageEditor && (
        <PageEditor
          page={editingPage}
          subjectId={selectedSubject?.id}
          onSave={() => {
            setShowPageEditor(false);
            fetchContentHub();
          }}
          onCancel={() => setShowPageEditor(false)}
        />
      )}
    </div>
  );
}
