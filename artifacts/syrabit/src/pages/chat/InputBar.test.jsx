import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, fireEvent, waitFor, act } from '@testing-library/react';
import axios from 'axios';
import { toast } from 'sonner';
import { InputBar } from './InputBar';

vi.mock('axios');
vi.mock('sonner', () => ({
  toast: { error: vi.fn(), success: vi.fn() },
}));
vi.mock('react-router-dom', () => ({ useNavigate: () => vi.fn() }));
vi.mock('@/utils/api', () => ({ API_BASE: '/api', getAnonId: () => 'anon-test' }));
vi.mock('@/hooks/useTTS', () => ({ getTTSLang: () => 'en' }));
vi.mock('@/components/study/MicButton', () => ({ MicButton: () => null }));

URL.createObjectURL = () => 'blob:test-url';
URL.revokeObjectURL = () => {};

const MAX_IMAGE_BYTES = 8 * 1024 * 1024;

function makeJpegFile(size = 200) {
  const buf = new Uint8Array(size);
  buf[0] = 0xff; buf[1] = 0xd8; buf[2] = 0xff; buf[3] = 0xe0;
  return new File([buf], 'question.jpg', { type: 'image/jpeg' });
}

function makeDefaultProps(overrides = {}) {
  const textareaRef = { current: null };
  return {
    subject: null,
    messages: [],
    scopedChapters: [],
    input: '',
    setInput: vi.fn(),
    isLoading: false,
    isOutOfCredits: false,
    isLow: false,
    credits: 100,
    effectiveLimit: null,
    remaining: null,
    creditPercent: 100,
    textareaRef,
    adjustTextarea: vi.fn(),
    sendMsg: vi.fn(),
    handleStop: vi.fn(),
    isAnon: false,
    getTurnstileToken: vi.fn(),
    turnstileEnabled: false,
    activeChapter: null,
    onDismissChapter: vi.fn(),
    ...overrides,
  };
}

describe('InputBar — OCR image upload', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('calls OCR API and appends extracted text to composer on valid image selection', async () => {
    axios.post.mockResolvedValueOnce({ data: { text: 'What is photosynthesis?' } });

    const props = makeDefaultProps();
    render(<InputBar {...props} />);

    const galleryInput = document.querySelector('[data-testid="chat-gallery-input"]');
    const file = makeJpegFile();

    await act(async () => {
      fireEvent.change(galleryInput, { target: { files: [file] } });
    });

    await waitFor(() => {
      expect(axios.post).toHaveBeenCalledWith(
        expect.stringContaining('/ai/ocr-image'),
        expect.any(FormData),
        expect.objectContaining({ withCredentials: true }),
      );
    });

    await waitFor(() => {
      expect(props.setInput).toHaveBeenCalled();
    });

    const setInputArg = props.setInput.mock.calls[0][0];
    const result = typeof setInputArg === 'function' ? setInputArg('') : setInputArg;
    expect(result).toBe('What is photosynthesis?');

    expect(toast.success).toHaveBeenCalledWith(
      expect.stringMatching(/text extracted/i),
    );
  });

  it('shows "Reading text…" label in the image chip while OCR is in progress', async () => {
    let resolveOcr;
    axios.post.mockReturnValueOnce(
      new Promise((res) => { resolveOcr = res; }),
    );

    const props = makeDefaultProps();
    const { getByText } = render(<InputBar {...props} />);

    const galleryInput = document.querySelector('[data-testid="chat-gallery-input"]');

    act(() => {
      fireEvent.change(galleryInput, { target: { files: [makeJpegFile()] } });
    });

    await waitFor(() => {
      expect(getByText('Reading text…')).toBeInTheDocument();
    });

    await act(async () => {
      resolveOcr({ data: { text: 'Resolved text' } });
    });
  });

  it('shows error toast and does NOT call API when file exceeds 8 MB', async () => {
    const props = makeDefaultProps();
    render(<InputBar {...props} />);

    const galleryInput = document.querySelector('[data-testid="chat-gallery-input"]');
    const bigFile = new File(
      [new Uint8Array(MAX_IMAGE_BYTES + 1)],
      'big.jpg',
      { type: 'image/jpeg' },
    );

    await act(async () => {
      fireEvent.change(galleryInput, { target: { files: [bigFile] } });
    });

    expect(axios.post).not.toHaveBeenCalled();
    expect(toast.error).toHaveBeenCalledWith(
      expect.stringMatching(/too large/i),
    );
  });

  it('shows error toast when OCR API call fails without leaving a broken attachment', async () => {
    const apiError = Object.assign(new Error('server error'), {
      response: { data: { detail: 'Could not read the image.' } },
    });
    axios.post.mockRejectedValueOnce(apiError);

    const props = makeDefaultProps();
    const { queryByTestId } = render(<InputBar {...props} />);

    const galleryInput = document.querySelector('[data-testid="chat-gallery-input"]');

    await act(async () => {
      fireEvent.change(galleryInput, { target: { files: [makeJpegFile()] } });
    });

    await waitFor(() => {
      expect(toast.error).toHaveBeenCalledWith('Could not read the image.');
    });

    expect(queryByTestId('chat-image-preview')).not.toBeInTheDocument();
  });
});
