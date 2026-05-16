import { useState, useEffect, useCallback, useRef } from 'react';
import { useNavigate } from 'react-router-dom';
import axios from 'axios';
import { toast } from 'sonner';
import {
  Send, Square, Plus, Camera, Image as ImageIcon, X, Loader2, BookOpen,
} from 'lucide-react';
import { MicButton } from '@/components/study/MicButton';
import { getTTSLang } from '@/hooks/useTTS';
import { API_BASE, getAnonId } from '@/utils/api';

const ALLOWED_IMAGE_MIME = /^image\//i;
const MAX_IMAGE_BYTES = 8 * 1024 * 1024;

export function InputBar({
  subject, messages, scopedChapters, input, setInput,
  isLoading, isOutOfCredits, isLow, credits,
  effectiveLimit, remaining, creditPercent,
  textareaRef, adjustTextarea, sendMsg, handleStop,
  isAnon,
  activeChapter, onDismissChapter,
  responseLang,
}) {
  const navigate = useNavigate();
  const [maxTextareaHeight, setMaxTextareaHeight] = useState(160);
  const [showAttachMenu, setShowAttachMenu] = useState(false);
  const [attachedImage, setAttachedImage] = useState(null); // { file, previewUrl, name }
  const [ocrLoading, setOcrLoading] = useState(false);
  const cameraInputRef = useRef(null);
  const galleryInputRef = useRef(null);
  const attachMenuRef = useRef(null);
  const plusBtnRef = useRef(null);

  const updateMaxHeight = useCallback(() => {
    if (window.visualViewport) {
      const vpHeight = window.visualViewport.height;
      const newMax = vpHeight < 500 ? 80 : vpHeight < 700 ? 120 : 160;
      setMaxTextareaHeight(newMax);
    }
  }, []);

  useEffect(() => {
    updateMaxHeight();
    if (window.visualViewport) {
      window.visualViewport.addEventListener('resize', updateMaxHeight);
      return () => window.visualViewport.removeEventListener('resize', updateMaxHeight);
    }
  }, [updateMaxHeight]);

  // Close the attach menu on outside click / Escape.
  useEffect(() => {
    if (!showAttachMenu) return undefined;
    const onDown = (e) => {
      if (
        attachMenuRef.current && !attachMenuRef.current.contains(e.target) &&
        plusBtnRef.current && !plusBtnRef.current.contains(e.target)
      ) {
        setShowAttachMenu(false);
      }
    };
    const onKey = (e) => { if (e.key === 'Escape') setShowAttachMenu(false); };
    document.addEventListener('mousedown', onDown);
    document.addEventListener('touchstart', onDown);
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('mousedown', onDown);
      document.removeEventListener('touchstart', onDown);
      document.removeEventListener('keydown', onKey);
    };
  }, [showAttachMenu]);

  // Revoke object URL when the attached image changes / unmounts.
  useEffect(() => {
    return () => {
      if (attachedImage?.previewUrl) URL.revokeObjectURL(attachedImage.previewUrl);
    };
  }, [attachedImage]);

  const clearAttachedImage = useCallback(() => {
    setAttachedImage((cur) => {
      if (cur?.previewUrl) URL.revokeObjectURL(cur.previewUrl);
      return null;
    });
    if (cameraInputRef.current) cameraInputRef.current.value = '';
    if (galleryInputRef.current) galleryInputRef.current.value = '';
  }, []);

  const handleImagePicked = useCallback(async (file) => {
    if (!file) return;
    if (!ALLOWED_IMAGE_MIME.test(file.type)) {
      toast.error('Please select an image file (JPEG, PNG, WebP, GIF or HEIC).');
      return;
    }
    if (file.size > MAX_IMAGE_BYTES) {
      toast.error('Image too large — please pick one under 8 MB.');
      return;
    }
    const previewUrl = URL.createObjectURL(file);
    setAttachedImage({ file, previewUrl, name: file.name || 'image' });
    setOcrLoading(true);

    try {
      const fd = new FormData();
      fd.append('file', file, file.name || 'image');
      const headers = { 'Content-Type': 'multipart/form-data' };
      if (isAnon) {
        try { headers['x-anon-id'] = getAnonId(); } catch {}
      }
      const { data } = await axios.post(`${API_BASE}/ai/ocr-image`, fd, {
        withCredentials: true,
        headers,
        timeout: 60000,
      });
      const extracted = (data?.text || '').trim();
      if (!extracted) {
        toast.error('No readable text found in the image. You can still type your question.');
      } else {
        // Insert the extracted text into the composer. If the user already typed
        // something, append the OCR block beneath it so their question stays on top.
        setInput((prev) => {
          const trimmed = (prev || '').trim();
          if (!trimmed) return extracted;
          return `${trimmed}\n\n${extracted}`;
        });
        // Re-measure the textarea now that we've populated it.
        setTimeout(() => adjustTextarea && adjustTextarea(), 0);
        toast.success('Text extracted from image — review and send.');
      }
    } catch (err) {
      const detail = err?.response?.data?.detail || err?.message || 'Could not read the image.';
      toast.error(typeof detail === 'string' ? detail : 'Could not read the image.');
      // Drop the attachment on failure so the user isn't stuck with a broken chip.
      clearAttachedImage();
    } finally {
      setOcrLoading(false);
    }
  }, [setInput, adjustTextarea, clearAttachedImage]);

  const onCameraChange = (e) => {
    const f = e.target.files && e.target.files[0];
    setShowAttachMenu(false);
    handleImagePicked(f);
  };
  const onGalleryChange = (e) => {
    const f = e.target.files && e.target.files[0];
    setShowAttachMenu(false);
    handleImagePicked(f);
  };

  const handleSend = useCallback(() => {
    if (ocrLoading) return;
    sendMsg(input);
    // Clear the attachment after sending — the OCR text is already in the message.
    clearAttachedImage();
  }, [ocrLoading, sendMsg, input, clearAttachedImage]);

  // F2: Speculative warm-query — pre-fetch RAG chapters while the user is typing.
  // Fires 800 ms after the last keystroke when ≥15 chars are present and a
  // subject is selected. Best-effort: errors are silently discarded.
  useEffect(() => {
    const q = (input || '').trim();
    if (q.length < 15 || !subject?.id || isLoading) return;
    const timer = setTimeout(() => {
      fetch(`${API_BASE}/ai/warm-query`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'include',
        body: JSON.stringify({ query: q, subject_id: subject.id, subject_name: subject.name }),
      }).catch(() => {});
    }, 800);
    return () => clearTimeout(timer);
  }, [input, subject, isLoading]);

  const sendDisabled = !input.trim() || isOutOfCredits || ocrLoading;

  return (
    <div
      className="fixed bottom-0 left-0 right-0 z-20 border-t border-border/50 px-4 md:px-6 py-3 pb-[calc(0.75rem+68px+env(safe-area-inset-bottom,0px))] md:pb-3"
      style={{ background: 'var(--card)', backdropFilter: 'blur(12px)', WebkitBackdropFilter: 'blur(12px)' }}
      data-testid="chat-input"
    >
      <div className="max-w-3xl mx-auto">
        {/* Hidden file inputs — strictly image-only, never accept docs. */}
        <input
          ref={cameraInputRef}
          type="file"
          accept="image/*"
          capture="environment"
          className="hidden"
          onChange={onCameraChange}
          data-testid="chat-camera-input"
        />
        <input
          ref={galleryInputRef}
          type="file"
          accept="image/*"
          className="hidden"
          onChange={onGalleryChange}
          data-testid="chat-gallery-input"
        />

        {/* Active chapter context chip — shows which content card is grounding the RAG. */}
        {activeChapter && (
          <div className="mb-2 flex items-center gap-2" data-testid="chat-chapter-chip">
            <div
              className="inline-flex items-center gap-1.5 pl-2.5 pr-1.5 py-1 rounded-full border text-xs font-medium"
              style={{
                borderColor: 'rgba(139,92,246,0.30)',
                background: 'rgba(124,58,237,0.08)',
                color: '#a78bfa',
              }}
            >
              <BookOpen size={12} aria-hidden="true" style={{ flexShrink: 0 }} />
              <span className="max-w-[240px] truncate" title={activeChapter.title}>
                {activeChapter.title}
              </span>
              <button
                type="button"
                onClick={onDismissChapter}
                className="ml-0.5 w-4 h-4 rounded-full flex items-center justify-center hover:bg-black/10 transition-colors flex-shrink-0"
                aria-label={`Remove ${activeChapter.title} filter`}
                data-testid="chat-chapter-chip-dismiss"
              >
                <X size={10} aria-hidden="true" />
              </button>
            </div>
          </div>
        )}

        {/* Attached-image preview chip (ChatGPT-style, sits above the composer). */}
        {attachedImage && (
          <div className="mb-2 flex items-center gap-2" data-testid="chat-image-preview">
            <div
              className="relative inline-flex items-center gap-2 pl-1.5 pr-2 py-1.5 rounded-xl border"
              style={{ borderColor: 'rgba(139,92,246,0.25)', background: 'rgba(124,58,237,0.06)' }}
            >
              <div className="relative w-10 h-10 rounded-lg overflow-hidden flex-shrink-0" style={{ background: 'rgba(0,0,0,0.05)' }}>
                <img
                  src={attachedImage.previewUrl}
                  alt={attachedImage.name}
                  className="w-full h-full object-cover"
                />
                {ocrLoading && (
                  <div
                    className="absolute inset-0 flex items-center justify-center"
                    style={{ background: 'rgba(0,0,0,0.45)' }}
                  >
                    <Loader2 size={16} className="animate-spin text-white" aria-hidden="true" />
                  </div>
                )}
              </div>
              <div className="flex flex-col min-w-0 max-w-[160px] sm:max-w-[220px]">
                <span className="text-xs font-medium text-foreground truncate" title={attachedImage.name}>
                  {attachedImage.name}
                </span>
                <span className="text-[10px] text-muted-foreground">
                  {ocrLoading ? 'Reading text…' : 'Text added to message'}
                </span>
              </div>
              <button
                type="button"
                onClick={clearAttachedImage}
                disabled={ocrLoading}
                className="ml-1 w-6 h-6 rounded-full flex items-center justify-center hover:bg-black/10 disabled:opacity-50"
                aria-label="Remove attached image"
                data-testid="chat-image-remove"
              >
                <X size={14} aria-hidden="true" />
              </button>
            </div>
          </div>
        )}

        <div
          className="relative flex items-end gap-2 p-2.5 pl-2 rounded-3xl border transition-all duration-200"
          style={
            isOutOfCredits
              ? { borderColor: 'rgba(239,68,68,0.20)', opacity: 0.6, background: 'rgba(239,68,68,0.02)' }
              : { borderColor: 'rgba(139,92,246,0.15)', background: 'rgba(124,58,237,0.03)' }
          }
        >
          {/* Plus / attach button — opens image picker menu. */}
          <div className="relative flex-shrink-0">
            <button
              ref={plusBtnRef}
              type="button"
              onClick={() => setShowAttachMenu((v) => !v)}
              disabled={isOutOfCredits || ocrLoading}
              className="w-10 h-10 rounded-full flex items-center justify-center transition-all disabled:cursor-not-allowed disabled:opacity-50"
              style={{
                background: showAttachMenu ? 'rgba(124,58,237,0.12)' : 'rgba(124,58,237,0.06)',
                color: '#7c3aed',
                border: '1px solid rgba(139,92,246,0.20)',
              }}
              aria-label="Attach image"
              aria-expanded={showAttachMenu}
              aria-haspopup="menu"
              title="Attach image"
              data-testid="chat-attach-button"
            >
              <Plus size={18} aria-hidden="true" />
            </button>

            {showAttachMenu && (
              <div
                ref={attachMenuRef}
                role="menu"
                className="absolute bottom-full left-0 mb-2 min-w-[200px] rounded-2xl border shadow-lg overflow-hidden"
                style={{
                  background: 'var(--card)',
                  borderColor: 'rgba(139,92,246,0.20)',
                  boxShadow: '0 10px 30px rgba(0,0,0,0.12)',
                }}
                data-testid="chat-attach-menu"
              >
                <button
                  type="button"
                  role="menuitem"
                  onClick={() => cameraInputRef.current && cameraInputRef.current.click()}
                  className="w-full flex items-center gap-3 px-3.5 py-2.5 text-sm text-foreground hover:bg-[rgba(124,58,237,0.06)] transition-colors"
                  data-testid="chat-attach-camera"
                >
                  <Camera size={18} style={{ color: '#7c3aed' }} aria-hidden="true" />
                  <span>Take photo</span>
                </button>
                <div className="h-px" style={{ background: 'rgba(139,92,246,0.10)' }} />
                <button
                  type="button"
                  role="menuitem"
                  onClick={() => galleryInputRef.current && galleryInputRef.current.click()}
                  className="w-full flex items-center gap-3 px-3.5 py-2.5 text-sm text-foreground hover:bg-[rgba(124,58,237,0.06)] transition-colors"
                  data-testid="chat-attach-gallery"
                >
                  <ImageIcon size={18} style={{ color: '#7c3aed' }} aria-hidden="true" />
                  <span>Upload from gallery</span>
                </button>
              </div>
            )}
          </div>

          <textarea
            ref={textareaRef}
            value={input}
            onChange={(e) => { setInput(e.target.value); adjustTextarea(); }}
            onKeyDown={(e) => {
              if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault();
                handleSend();
              }
            }}
            placeholder={
              ocrLoading
                ? 'Reading image…'
                : isOutOfCredits
                ? (isAnon
                    ? 'Free daily messages used — sign in to keep chatting'
                    : 'No credits remaining — upgrade to continue')
                : activeChapter
                ? (responseLang === 'as'
                    ? `${activeChapter.title} সম্পৰ্কে প্ৰশ্ন সুধিব…`
                    : `Ask about ${activeChapter.title}…`)
                : subject
                ? (responseLang === 'as'
                    ? `${subject.name} সম্পৰ্কে অসমীয়াত প্ৰশ্ন সুধিব…`
                    : `Ask about ${subject.name}…`)
                : (responseLang === 'as'
                    ? 'অসমীয়াত প্ৰশ্ন সুধিব...'
                    : 'Ask anything about your Syllabus...')
            }
            disabled={isOutOfCredits}
            rows={1}
            className="flex-1 bg-transparent resize-none outline-none text-sm text-foreground placeholder:text-muted-foreground disabled:cursor-not-allowed py-2"
            style={{ minHeight: 24, maxHeight: maxTextareaHeight }}
            aria-label="Type your message"
          />
          <div className="flex items-center gap-1.5 flex-shrink-0">
            <MicButton
              language={getTTSLang() === 'as' ? 'as-IN' : 'en-IN'}
              disabled={isOutOfCredits || isLoading || ocrLoading}
              onTranscript={(text) => {
                if (!text) return;
                setInput((prev) => (prev ? prev + ' ' : '') + text);
                setTimeout(() => adjustTextarea && adjustTextarea(), 0);
              }}
            />
            {isLoading ? (
              <button
                onClick={handleStop}
                className="w-10 h-10 rounded-full flex items-center justify-center transition-all"
                style={{
                  background: 'rgba(239,68,68,0.15)',
                  border: '1px solid rgba(239,68,68,0.30)',
                  color: '#f87171',
                }}
                aria-label="Stop generating"
                title="Stop"
                data-testid="chat-stop-button"
              >
                <Square size={14} aria-hidden="true" />
              </button>
            ) : (
              <button
                onClick={handleSend}
                disabled={sendDisabled}
                className="w-10 h-10 rounded-full flex items-center justify-center transition-all disabled:cursor-not-allowed"
                style={
                  !sendDisabled
                    ? {
                        background: 'linear-gradient(135deg,#7c3aed,#8b5cf6)',
                        color: '#fff',
                        boxShadow: '0 4px 15px rgba(139,92,246,0.4)',
                      }
                    : { background: 'hsl(var(--muted))', color: 'hsl(var(--muted-foreground))' }
                }
                data-testid="chat-send-button"
                aria-label="Send message"
                title={ocrLoading ? 'Reading image…' : 'Send'}
              >
                {ocrLoading
                  ? <Loader2 size={16} className="animate-spin" aria-hidden="true" />
                  : <Send size={16} aria-hidden="true" />}
              </button>
            )}
          </div>
        </div>

        {effectiveLimit !== null && effectiveLimit > 0 && (
          <div className="mt-2 px-1 flex items-center gap-2">
            <div
              className="flex-1 h-1 rounded-full overflow-hidden"
              style={{ background: 'rgba(139,92,246,0.10)' }}
              role="progressbar"
              aria-valuenow={creditPercent}
              aria-valuemin={0}
              aria-valuemax={100}
              aria-label="Credit usage"
            >
              <div
                className="h-full rounded-full transition-all duration-500"
                style={{
                  width: `${creditPercent}%`,
                  background: isLow || isOutOfCredits
                    ? 'linear-gradient(90deg,#ef4444,#f87171)'
                    : 'linear-gradient(90deg,#7c3aed,#a78bfa)',
                }}
              />
            </div>
            {/*
              Task #796 — anonymous students get a slightly more
              explicit "X / 30 free messages left today" caption (the
              previous "12 left" was cryptic to first-time visitors who
              had never seen the cap), plus a tiny "Sign in for more"
              CTA once they're at the half-way mark. Logged-in users
              keep the compact "X left" badge they're used to.
            */}
            {isAnon ? (
              <span
                className="text-[10px] font-medium shrink-0 flex items-center gap-1.5"
                style={{ color: isLow || isOutOfCredits ? '#f87171' : 'hsl(var(--muted-foreground))' }}
              >
                <span data-testid="anon-credits-remaining">
                  {remaining !== null
                    ? (isOutOfCredits
                        ? `0 / ${effectiveLimit} free messages left today`
                        : `${remaining} / ${effectiveLimit} free messages left today`)
                    : ''}
                </span>
                {remaining !== null && remaining <= Math.ceil(effectiveLimit / 2) && (
                  <button
                    type="button"
                    onClick={() => navigate('/login')}
                    className="font-semibold underline hover:no-underline"
                    style={{ color: isOutOfCredits || isLow ? '#fca5a5' : '#a78bfa' }}
                    data-testid="anon-credits-signin-cta"
                    aria-label="Sign in for more messages"
                  >
                    {isOutOfCredits ? 'Sign in →' : 'Sign in for more'}
                  </button>
                )}
              </span>
            ) : (
              <span
                className="text-[10px] font-medium shrink-0"
                style={{ color: isLow || isOutOfCredits ? '#f87171' : 'hsl(var(--muted-foreground))' }}
              >
                {remaining !== null ? `${remaining} left` : ''}
              </span>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
