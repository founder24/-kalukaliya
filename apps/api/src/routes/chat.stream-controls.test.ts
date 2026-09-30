import { describe, expect, it } from 'vitest';

import {
  buildChatStreamFailureDiagnostic,
  shouldPollChatCancellation,
} from './chat';

describe('stream cancellation polling', () => {
  it('bounds D1 tombstone checks during bursts of streamed chunks', () => {
    let lastCheckedAt = 0;
    const checks: number[] = [];

    for (let now = 100; now <= 1_000; now += 100) {
      if (shouldPollChatCancellation(now, lastCheckedAt)) {
        checks.push(now);
        lastCheckedAt = now;
      }
    }

    expect(checks).toEqual([500, 1_000]);
  });

  it('polls again once the fallback interval has elapsed', () => {
    expect(shouldPollChatCancellation(1_499, 1_000)).toBe(false);
    expect(shouldPollChatCancellation(1_500, 1_000)).toBe(true);
  });
});

describe('chat stream failure diagnostics', () => {
  it('records partial output and terminal-marker state without logging text', () => {
    const privateContent = 'student prompt and generated completion';
    const diagnostic = buildChatStreamFailureDiagnostic(
      new Error(privateContent),
      {
        failureStage: 'provider_stream',
        elapsedMs: 1_234.6,
        tokensEmitted: true,
        terminalSseMarkerWritten: true,
      },
    );

    expect(diagnostic).toEqual({
      error_class: 'Error',
      error_code: 'provider_exception',
      failure_stage: 'provider_stream',
      elapsed_ms: 1_235,
      tokens_emitted: true,
      terminal_sse_marker_written: true,
    });
    expect(JSON.stringify(diagnostic)).not.toContain(privateContent);
  });

  it('reduces provider exception names and codes to safe diagnostics', () => {
    const providerError = Object.assign(
      new Error('credential=do-not-log; completion=private'),
      { name: 'ProviderSDKError', code: 'credential-value' },
    );
    const diagnostic = buildChatStreamFailureDiagnostic(providerError, {
      failureStage: 'provider_stream',
      elapsedMs: 2_500,
      tokensEmitted: false,
      terminalSseMarkerWritten: false,
    });

    expect(diagnostic.error_class).toBe('ProviderError');
    expect(diagnostic.error_code).toBe('provider_exception');
    expect(JSON.stringify(diagnostic)).not.toContain('credential');
    expect(JSON.stringify(diagnostic)).not.toContain('private');
  });

  it('classifies provider timeout separately from generic failures', () => {
    const timeoutError = new Error('timeout detail must not be logged');
    timeoutError.name = 'TimeoutError';

    const diagnostic = buildChatStreamFailureDiagnostic(timeoutError, {
      failureStage: 'provider_stream',
      elapsedMs: 3_000,
      tokensEmitted: true,
      terminalSseMarkerWritten: false,
    });

    expect(diagnostic.error_class).toBe('TimeoutError');
    expect(diagnostic.error_code).toBe('timeout');
    expect(diagnostic.tokens_emitted).toBe(true);
    expect(diagnostic.terminal_sse_marker_written).toBe(false);
  });

  it('classifies client cancellation without claiming a terminal marker', () => {
    const cancellation = new DOMException('student content is not logged', 'AbortError');
    const diagnostic = buildChatStreamFailureDiagnostic(cancellation, {
      failureStage: 'provider_stream',
      elapsedMs: 900,
      tokensEmitted: true,
      terminalSseMarkerWritten: false,
      abortSource: 'request_signal',
    });

    expect(diagnostic.error_class).toBe('AbortError');
    expect(diagnostic.error_code).toBe('client_cancelled');
    expect(diagnostic.abort_source).toBe('request_signal');
    expect(diagnostic.tokens_emitted).toBe(true);
    expect(diagnostic.terminal_sse_marker_written).toBe(false);
    expect(JSON.stringify(diagnostic)).not.toContain('student content');
  });

  it('distinguishes downstream stream closure from request-signal cancellation', () => {
    const cancellation = new DOMException('private stream detail', 'AbortError');
    const diagnostic = buildChatStreamFailureDiagnostic(cancellation, {
      failureStage: 'stream_write',
      elapsedMs: 1_100,
      tokensEmitted: true,
      terminalSseMarkerWritten: false,
      abortSource: 'response_stream',
    });

    expect(diagnostic.error_code).toBe('downstream_cancelled');
    expect(diagnostic.abort_source).toBe('response_stream');
    expect(JSON.stringify(diagnostic)).not.toContain('private stream detail');
  });
});