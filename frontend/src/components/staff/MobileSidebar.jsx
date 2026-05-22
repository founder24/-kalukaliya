/**
 * Mobile Sidebar Component for Staff CMS
 * Slide-out drawer navigation for mobile devices
 */

import { X, BookOpen, ChevronRight, ChevronDown, Edit2, Lock } from 'lucide-react';

export default function MobileSidebar({
  isOpen,
  onClose,
  boards = [],
  classes = [],
  subjects = [],
  expandedBoards = {},
  expandedClasses = {},
  toggleBoard,
  toggleClass,
  selectedBoard,
  selectedClass,
  selectedSubject,
  setSelectedBoard,
  setSelectedClass,
  setSelectedSubject,
  permissions = {},
}) {
  if (!isOpen) return null;

  return (
    <div className="fixed inset-0 z-50 lg:hidden">
      {/* Sidebar Panel */}
      <div className="absolute left-0 top-0 bottom-0 w-72 bg-white shadow-xl overflow-y-auto">
        {/* Header */}
        <div className="flex items-center justify-between p-4 border-b sticky top-0 bg-white z-10">
          <div>
            <h2 className="text-lg font-bold text-purple-700">Syrabit.ai</h2>
            <p className="text-xs text-gray-500">Staff Portal</p>
          </div>
          <button
            onClick={onClose}
            className="p-2 rounded-lg hover:bg-gray-100"
          >
            <X size={20} className="text-gray-700" />
          </button>
        </div>

        {/* Navigation */}
        <nav className="p-4 space-y-2">
          <h3 className="text-xs font-semibold text-gray-500 uppercase tracking-wider mb-3">
            Content Structure
          </h3>

          {boards.map(board => (
            <div key={board.id} className="border rounded-lg overflow-hidden">
              <button
                onClick={() => toggleBoard(board.id)}
                className="w-full flex items-center justify-between p-3 bg-gray-50 hover:bg-gray-100"
                disabled={!permissions.canEditBoards}
              >
                <div className="flex items-center gap-2">
                  <BookOpen size={16} className="text-blue-500" />
                  <span className="font-medium text-sm">{board.name}</span>
                  {!permissions.canEditBoards && (
                    <Lock size={12} className="text-gray-400" />
                  )}
                </div>
                {expandedBoards[board.id] ? (
                  <ChevronDown size={16} className="text-gray-400" />
                ) : (
                  <ChevronRight size={16} className="text-gray-400" />
                )}
              </button>

              {expandedBoards[board.id] && (
                <div className="bg-white">
                  {classes
                    .filter(c => c.board_id === board.id)
                    .map(cls => (
                      <div key={cls.id}>
                        <button
                          onClick={() => toggleClass(cls.id)}
                          className="w-full flex items-center justify-between p-2 pl-8 hover:bg-gray-50 text-sm"
                          disabled={!permissions.canEditClasses}
                        >
                          <span className="text-gray-700">{cls.name}</span>
                          {!permissions.canEditClasses && (
                            <Lock size={12} className="text-gray-400" />
                          )}
                          {expandedClasses[cls.id] ? (
                            <ChevronDown size={14} className="text-gray-400" />
                          ) : (
                            <ChevronRight size={14} className="text-gray-400" />
                          )}
                        </button>

                        {expandedClasses[cls.id] && (
                          <div className="pl-4 pb-2 space-y-1">
                            {subjects
                              .filter(s => s.class_id === cls.id)
                              .map(subject => (
                                <button
                                  key={subject.id}
                                  onClick={() => setSelectedSubject(subject)}
                                  className={`w-full text-left px-3 py-2 rounded text-sm flex items-center justify-between ${
                                    selectedSubject?.id === subject.id
                                      ? 'bg-purple-100 text-purple-700'
                                      : 'hover:bg-gray-100 text-gray-600'
                                  }`}
                                >
                                  <span>{subject.name}</span>
                                  {permissions.canEditSubjects && (
                                    <Edit2 size={12} className="text-gray-400" />
                                  )}
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

          {boards.length === 0 && (
            <div className="text-center py-8 text-gray-400 text-sm">
              No boards available
            </div>
          )}
        </nav>

        {/* Permissions Info */}
        <div className="p-4 mt-4 border-t bg-gray-50">
          <h4 className="text-xs font-semibold text-gray-500 uppercase mb-2">
            Your Permissions
          </h4>
          <div className="space-y-1 text-xs">
            <div className="flex items-center gap-2">
              <span className={permissions.canEditSubjects ? 'text-green-600' : 'text-gray-400'}>
                ●
              </span>
              <span className="text-gray-600">Edit Subjects</span>
            </div>
            <div className="flex items-center gap-2">
              <span className={permissions.canCreatePages ? 'text-green-600' : 'text-gray-400'}>
                ●
              </span>
              <span className="text-gray-600">Create/Edit Pages</span>
            </div>
            <div className="flex items-center gap-2">
              <span className={permissions.canDeletePages ? 'text-green-600' : 'text-gray-400'}>
                ●
              </span>
              <span className="text-gray-600">Delete Pages</span>
            </div>
            <div className="flex items-center gap-2 text-gray-400">
              <span>●</span>
              <span>Boards & Classes (Read-only)</span>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
