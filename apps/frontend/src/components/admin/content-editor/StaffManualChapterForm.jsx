import { useState } from 'react';
import { ArrowLeft, BookOpen, CircleHelp, FileText, Info, Loader2, Save } from 'lucide-react';
import StaffChapterPYQUploadPanel from './StaffChapterPYQUploadPanel';
import { autoSlug } from '@/utils/adminHelpers';

const TABS = [
  { id: 'info', label: 'Info', icon: Info },
  { id: 'notes', label: 'Notes', icon: BookOpen },
  { id: 'questions', label: 'Questions', icon: CircleHelp },
  { id: 'pyq', label: 'PYQ', icon: FileText },
];

const CHAPTER_TYPES = [
  { value: 'notes', label: 'Notes' },
  { value: 'qa', label: 'Questions' },
  { value: 'question_paper', label: 'PYQ' },
  { value: 'formula', label: 'Formula Sheet' },
  { value: 'summary', label: 'Summary' },
  { value: 'solution', label: 'Solution' },
  { value: 'reference', label: 'Reference' },
];

const inputClass = 'w-full h-10 rounded-lg border border-gray-200 bg-white px-3 text-sm text-gray-900 outline-none transition focus:border-violet-500 focus:ring-2 focus:ring-violet-100';
const textAreaClass = 'w-full min-h-[320px] resize-y rounded-lg border border-gray-200 bg-white p-3 text-sm leading-relaxed text-gray-900 outline-none transition focus:border-violet-500 focus:ring-2 focus:ring-violet-100';

function Field({ label, children }) {
  return (
    <label className="block min-w-0">
      <span className="mb-1.5 block text-xs font-medium text-gray-600">{label}</span>
      {children}
    </label>
  );
}

export default function StaffManualChapterForm({
  editView,
  editTarget,
  contentForm,
  setContentForm,
  subjectData,
  saving,
  onSave,
  onCancel,
  adminToken,
}) {
  const [activeTab, setActiveTab] = useState('info');
  const update = (field, value) => setContentForm(previous => ({ ...previous, [field]: value }));
  const topicsValue = Array.isArray(contentForm.topics) ? contentForm.topics.join(', ') : '';

  return (
    <section className="flex h-full min-h-0 flex-col bg-white" data-testid="staff-manual-chapter-form">
      <header className="shrink-0 border-b border-gray-200 px-5 py-4 sm:px-6">
        <button type="button" onClick={onCancel} className="mb-3 inline-flex items-center gap-1.5 text-sm text-gray-500 hover:text-gray-900">
          <ArrowLeft size={15} /> Back to chapters
        </button>
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <h2 className="text-lg font-semibold text-gray-900">{editView === 'edit-chapter' ? 'Edit chapter' : 'Create chapter'}</h2>
            <p className="mt-1 text-xs text-gray-500">{subjectData?.name || 'Select a subject'}</p>
          </div>
          {editView === 'edit-chapter' && (
            <span className="rounded-full bg-gray-100 px-2.5 py-1 text-xs font-medium capitalize text-gray-600">
              {editTarget?.status || 'draft'}
            </span>
          )}
        </div>

        <div className="mt-4 flex gap-1 overflow-x-auto" role="tablist" aria-label="Chapter sections">
          {TABS.map(({ id, label, icon: Icon }) => (
            <button
              key={id}
              type="button"
              role="tab"
              id={`staff-chapter-tab-${id}`}
              aria-selected={activeTab === id}
              aria-controls={`staff-chapter-panel-${id}`}
              data-testid={`staff-chapter-tab-${id}`}
              onClick={() => setActiveTab(id)}
              className={`inline-flex shrink-0 items-center gap-2 rounded-t-lg border-b-2 px-3 py-2.5 text-sm font-medium transition ${
                activeTab === id
                  ? 'border-violet-600 bg-violet-50 text-violet-700'
                  : 'border-transparent text-gray-500 hover:bg-gray-50 hover:text-gray-800'
              }`}
            >
              <Icon size={15} />
              {label}
            </button>
          ))}
        </div>
      </header>

      <div className="min-h-0 flex-1 overflow-y-auto px-5 py-5 sm:px-6">
        {activeTab === 'info' && (
          <div id="staff-chapter-panel-info" role="tabpanel" aria-labelledby="staff-chapter-tab-info" className="space-y-5">
            <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
              <Field label="Title — English *">
                <input
                  value={contentForm.title || ''}
                  onChange={(event) => {
                    const title = event.target.value;
                    setContentForm(previous => ({
                      ...previous,
                      title,
                      slug: !previous.slug || previous.slug === autoSlug(previous.title)
                        ? autoSlug(title)
                        : previous.slug,
                    }));
                  }}
                  className={inputClass}
                  placeholder="Chapter title"
                  autoComplete="off"
                  data-testid="staff-chapter-title-en"
                />
              </Field>
              <Field label="Title — অসমীয়া">
                <input value={contentForm.title_as || ''} onChange={(event) => update('title_as', event.target.value)} className={inputClass} placeholder="অধ্যায়ৰ শিৰোনাম" data-testid="staff-chapter-title-as" />
              </Field>
              <Field label="URL slug — English">
                <input value={contentForm.slug || ''} onChange={(event) => update('slug', event.target.value)} className={inputClass} placeholder="chapter-slug" data-testid="staff-chapter-slug-en" />
              </Field>
              <Field label="URL slug — অসমীয়া">
                <input value={contentForm.slug_as || ''} onChange={(event) => update('slug_as', event.target.value)} className={inputClass} placeholder="অসমীয়া-শ্লাগ" data-testid="staff-chapter-slug-as" />
              </Field>
              <Field label="Chapter number">
                <input
                  type="number"
                  min="1"
                  step="1"
                  value={contentForm.order ?? ''}
                  onChange={(event) => update('order', event.target.value === '' ? '' : Number(event.target.value))}
                  className={inputClass}
                  placeholder="Assigned automatically when blank"
                  data-testid="staff-chapter-number"
                />
              </Field>
              <Field label="Content type">
                <select value={contentForm.content_type || 'notes'} onChange={(event) => update('content_type', event.target.value)} className={inputClass} data-testid="staff-chapter-content-type">
                  {CHAPTER_TYPES.map(type => <option key={type.value} value={type.value}>{type.label}</option>)}
                </select>
              </Field>
            </div>

            <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
              <Field label="Meta description — English">
                <textarea value={contentForm.meta_description || ''} onChange={(event) => update('meta_description', event.target.value)} className={`${textAreaClass} !min-h-24`} rows={3} placeholder="Short chapter description" data-testid="staff-chapter-meta-en" />
              </Field>
              <Field label="Meta description — অসমীয়া">
                <textarea value={contentForm.meta_description_as || ''} onChange={(event) => update('meta_description_as', event.target.value)} className={`${textAreaClass} !min-h-24`} rows={3} placeholder="অধ্যায়ৰ চমু বিৱৰণ" data-testid="staff-chapter-meta-as" />
              </Field>
              <Field label="Keywords — English">
                <input value={contentForm.keywords || ''} onChange={(event) => update('keywords', event.target.value)} className={inputClass} placeholder="Comma-separated keywords" data-testid="staff-chapter-keywords-en" />
              </Field>
            </div>

            <Field label="Topics (comma-separated)">
              <input
                value={topicsValue}
                onChange={(event) => update('topics', event.target.value.split(',').map(topic => topic.trim()).filter(Boolean))}
                className={inputClass}
                placeholder="e.g. Photosynthesis, Carbon cycle"
                data-testid="staff-chapter-topics"
              />
            </Field>
          </div>
        )}

        {activeTab === 'notes' && (
          <div id="staff-chapter-panel-notes" role="tabpanel" aria-labelledby="staff-chapter-tab-notes" className="space-y-3">
            <div>
              <h3 className="text-sm font-semibold text-gray-900">Chapter notes</h3>
              <p className="mt-1 text-xs text-gray-500">Enter both language versions yourself. Markdown is supported; saving does not translate either field.</p>
            </div>
            <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
              <Field label="Notes — English">
                <textarea value={contentForm.notes_en ?? contentForm.content ?? ''} onChange={(event) => update('notes_en', event.target.value)} className={textAreaClass} placeholder="Write English notes…" data-testid="staff-chapter-notes-en" />
              </Field>
              <Field label="Notes — অসমীয়া">
                <textarea value={contentForm.notes_as ?? contentForm.content_as ?? ''} onChange={(event) => update('notes_as', event.target.value)} className={textAreaClass} placeholder="অসমীয়াত টোকা লিখক…" data-testid="staff-chapter-notes-as" />
              </Field>
            </div>
          </div>
        )}

        {activeTab === 'questions' && (
          <div id="staff-chapter-panel-questions" role="tabpanel" aria-labelledby="staff-chapter-tab-questions" className="space-y-3">
            <div>
              <h3 className="text-sm font-semibold text-gray-900">Chapter questions</h3>
              <p className="mt-1 text-xs text-gray-500">Enter English and Assamese questions and answers manually. Markdown headings and lists are supported.</p>
            </div>
            <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
              <Field label="Questions — English">
                <textarea value={contentForm.qa_text_en || ''} onChange={(event) => update('qa_text_en', event.target.value)} className={textAreaClass} placeholder="## Question 1&#10;Write the answer below…" data-testid="staff-chapter-questions-en" />
              </Field>
              <Field label="Questions — অসমীয়া">
                <textarea value={contentForm.qa_text_as || ''} onChange={(event) => update('qa_text_as', event.target.value)} className={textAreaClass} placeholder="## প্ৰশ্ন ১&#10;তলত উত্তৰ লিখক…" data-testid="staff-chapter-questions-as" />
              </Field>
            </div>
          </div>
        )}

        {activeTab === 'pyq' && (
          <div id="staff-chapter-panel-pyq" role="tabpanel" aria-labelledby="staff-chapter-tab-pyq">
            {editTarget?.id ? (
              <StaffChapterPYQUploadPanel
                chapterId={editTarget.id}
                adminToken={adminToken}
                initialPdfUrl={editTarget.pyq_pdf_url}
                initialPapers={editTarget.pyq_papers}
              />
            ) : (
              <div className="rounded-xl border border-amber-200 bg-amber-50 p-4 text-sm text-amber-900">
                Save the chapter first. You can then upload its PYQ paper PDF or scanned page images.
              </div>
            )}
          </div>
        )}
      </div>

      <footer className="flex shrink-0 justify-end gap-2 border-t border-gray-200 bg-white px-5 py-3 sm:px-6">
        <button type="button" onClick={onCancel} className="h-10 rounded-lg bg-gray-100 px-4 text-sm font-medium text-gray-700 hover:bg-gray-200">Cancel</button>
        <button
          type="button"
          onClick={onSave}
          disabled={saving || !contentForm.title?.trim()}
          className="inline-flex h-10 items-center gap-2 rounded-lg bg-violet-600 px-4 text-sm font-semibold text-white hover:bg-violet-700 disabled:cursor-not-allowed disabled:opacity-50"
          data-testid="staff-chapter-save"
        >
          {saving ? <Loader2 size={15} className="animate-spin" /> : <Save size={15} />}
          {saving ? 'Saving…' : editView === 'edit-chapter' ? 'Save changes' : 'Create chapter'}
        </button>
      </footer>
    </section>
  );
}