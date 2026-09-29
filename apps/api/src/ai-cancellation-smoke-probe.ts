import { generateAssamese, streamGenerate } from './services/ai';

interface ProbeEnv {
  AI: Ai;
  APP_ENV?: string;
  STAGING_ACCESS_TOKEN?: string;
}

type TimedResult<T> =
  | { kind: 'value'; value: T }
  | { kind: 'rejected'; error: unknown }
  | { kind: 'timeout' };

type BindingSettlement =
  | { settled: true; outcome: 'fulfilled'; settledAt: number }
  | { settled: true; outcome: 'rejected'; settledAt: number; errorName: string }
  | { settled: false; outcome: 'pending' | 'not_started' };

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: {
      'content-type': 'application/json; charset=utf-8',
      'cache-control': 'no-store',
    },
  });
}

function constantTimeEqual(left: string, right: string): boolean {
  const leftBytes = new TextEncoder().encode(left);
  const rightBytes = new TextEncoder().encode(right);
  if (leftBytes.length !== rightBytes.length) return false;

  let difference = 0;
  for (let index = 0; index < leftBytes.length; index += 1) {
    difference |= leftBytes[index] ^ rightBytes[index];
  }
  return difference === 0;
}

function errorName(error: unknown): string {
  return error instanceof Error ? error.name : 'NonError';
}

function waitWithin<T>(promise: Promise<T>, timeoutMs: number): Promise<TimedResult<T>> {
  return new Promise((resolve) => {
    const timer = setTimeout(() => resolve({ kind: 'timeout' }), timeoutMs);
    promise.then(
      (value) => {
        clearTimeout(timer);
        resolve({ kind: 'value', value });
      },
      (error: unknown) => {
        clearTimeout(timer);
        resolve({ kind: 'rejected', error });
      },
    );
  });
}

async function runStreamingProbe(env: ProbeEnv): Promise<Response> {
  const startedAt = Date.now();
  const controller = new AbortController();
  let bindingSignal: AbortSignal | undefined;
  let bindingReturnedStream = false;
  let bindingCancelStartedAt: number | undefined;
  let bindingCancelSettledAt: number | undefined;
  let bindingCancelErrorName: string | undefined;
  let bindingCancelPromise: Promise<void> | undefined;

  const invoke = env.AI.run.bind(env.AI) as unknown as (
    model: string,
    input: unknown,
    options?: { signal?: AbortSignal },
  ) => Promise<unknown>;

  const observedAi = {
    run: async (model: string, input: unknown, options?: { signal?: AbortSignal }) => {
      bindingSignal = options?.signal;
      const result = await invoke(model, input, options);
      if (!result || typeof (result as ReadableStream<Uint8Array>).getReader !== 'function') {
        return result;
      }

      bindingReturnedStream = true;
      const bindingReader = (result as ReadableStream<Uint8Array>).getReader();
      return new ReadableStream<Uint8Array>({
        async pull(controllerForStream) {
          try {
            const item = await bindingReader.read();
            if (item.done) controllerForStream.close();
            else controllerForStream.enqueue(item.value);
          } catch (error) {
            controllerForStream.error(error);
          }
        },
        cancel(reason) {
          if (!bindingCancelPromise) {
            bindingCancelStartedAt = Date.now();
            bindingCancelPromise = bindingReader.cancel(reason).then(
              () => {
                bindingCancelSettledAt = Date.now();
              },
              (error: unknown) => {
                bindingCancelErrorName = errorName(error);
                bindingCancelSettledAt = Date.now();
              },
            );
          }
          return bindingCancelPromise;
        },
      });
    },
  } as unknown as Ai;

  const generation = streamGenerate(observedAi, {
    systemPrompt: 'Write a detailed English explanation with examples and a short summary.',
    userMessage: 'Explain why seasons occur and how Earth’s tilt affects different latitudes.',
    maxTokens: 640,
    signal: controller.signal,
  });

  let firstDeltaReceived = false;
  let firstDeltaOutcome = 'pending';
  let firstDeltaMs: number | undefined;
  for (;;) {
    const next = await waitWithin(generation.next(), 20_000);
    if (next.kind === 'timeout') {
      firstDeltaOutcome = 'timeout';
      break;
    }
    if (next.kind === 'rejected') {
      firstDeltaOutcome = errorName(next.error);
      break;
    }
    if (next.value.done) {
      firstDeltaOutcome = 'completed_without_delta';
      break;
    }
    if (next.value.value.startsWith('\x00model:')) continue;
    if (next.value.value.length > 0) {
      firstDeltaReceived = true;
      firstDeltaOutcome = 'delta_received';
      firstDeltaMs = Date.now() - startedAt;
      break;
    }
  }

  controller.abort(new DOMException('Staging cancellation smoke check', 'AbortError'));
  const abortStartedAt = Date.now();
  const afterAbort = await waitWithin(generation.next(), 5000);
  const postAbortOutcome = afterAbort.kind === 'timeout'
    ? 'pending'
    : afterAbort.kind === 'rejected'
      ? 'rejected'
      : afterAbort.value.done
        ? 'done'
        : 'yielded';

  if (bindingCancelPromise) await waitWithin(bindingCancelPromise, 3000);

  return jsonResponse({
    target: 'staging',
    firstDeltaOutcome,
    firstDeltaReceived,
    firstDeltaMs,
    abortedAfterFirstDelta: firstDeltaReceived && controller.signal.aborted,
    abortReasonName: controller.signal.reason instanceof Error
      ? controller.signal.reason.name
      : undefined,
    postAbortOutcome,
    postAbortErrorName: afterAbort.kind === 'rejected' ? errorName(afterAbort.error) : undefined,
    postAbortMs: afterAbort.kind === 'timeout' ? undefined : Date.now() - abortStartedAt,
    bindingSignalAborted: bindingSignal?.aborted ?? false,
    bindingReturnedStream,
    bindingReaderCancelCalled: bindingCancelStartedAt !== undefined,
    bindingReaderCancelSettled: bindingCancelSettledAt !== undefined,
    bindingReaderCancelMs: bindingCancelStartedAt !== undefined && bindingCancelSettledAt !== undefined
      ? bindingCancelSettledAt - bindingCancelStartedAt
      : undefined,
    bindingReaderCancelErrorName: bindingCancelErrorName,
  });
}

async function runBufferedTimeoutProbe(env: ProbeEnv): Promise<Response> {
  const startedAt = Date.now();
  let bindingSignal: AbortSignal | undefined;
  let bindingCallMade = false;
  let resolveBindingSettlement!: (settlement: BindingSettlement) => void;
  const bindingSettlementPromise = new Promise<BindingSettlement>((resolve) => {
    resolveBindingSettlement = resolve;
  });

  const invoke = env.AI.run.bind(env.AI) as unknown as (
    model: string,
    input: unknown,
    options?: { signal?: AbortSignal },
  ) => Promise<unknown>;

  const observedAi = {
    run: (model: string, input: unknown, options?: { signal?: AbortSignal }) => {
      bindingCallMade = true;
      bindingSignal = options?.signal;
      const bindingPromise = Promise.resolve(invoke(model, input, options));
      void bindingPromise.then(
        () => resolveBindingSettlement({
          settled: true,
          outcome: 'fulfilled',
          settledAt: Date.now(),
        }),
        (error: unknown) => resolveBindingSettlement({
          settled: true,
          outcome: 'rejected',
          settledAt: Date.now(),
          errorName: errorName(error),
        }),
      );
      return bindingPromise;
    },
  } as unknown as Ai;

  const helperResult = await generateAssamese(observedAi, {
    systemPrompt: 'Respond only in Assamese. Give a detailed explanation with examples.',
    userMessage: 'বায়ুমণ্ডলত শব্দ কেনেকৈ সঞ্চাৰিত হয় সেই বিষয়ে উদাহৰণসহ ব্যাখ্যা লিখা।',
    maxTokens: 640,
  }, 500).then(
    () => ({
      status: 'fulfilled' as const,
      returnedAt: Date.now(),
      reportedTimeout: false,
    }),
    (error: unknown) => ({
      status: 'rejected' as const,
      returnedAt: Date.now(),
      reportedTimeout: error instanceof Error && error.name === 'TimeoutError',
      errorName: errorName(error),
    }),
  );

  const bindingSettlement = bindingCallMade
    ? await Promise.race([
      bindingSettlementPromise,
      new Promise<BindingSettlement>((resolve) => {
        setTimeout(() => resolve({ settled: false, outcome: 'pending' }), 3000);
      }),
    ])
    : { settled: false as const, outcome: 'not_started' as const };

  return jsonResponse({
    target: 'staging',
    helperStatus: helperResult.status,
    helperReportedTimeout: helperResult.reportedTimeout,
    helperErrorName: helperResult.status === 'rejected' ? helperResult.errorName : undefined,
    helperMs: helperResult.returnedAt - startedAt,
    timeoutSignalAborted: bindingSignal?.aborted ?? false,
    abortReasonName: bindingSignal?.reason instanceof Error
      ? bindingSignal.reason.name
      : undefined,
    bindingCallMade,
    bindingPromiseSettled: bindingSettlement.settled,
    bindingPromiseOutcome: bindingSettlement.outcome,
    bindingErrorName: bindingSettlement.settled && bindingSettlement.outcome === 'rejected'
      ? bindingSettlement.errorName
      : undefined,
    bindingSettledMs: bindingSettlement.settled
      ? bindingSettlement.settledAt - startedAt
      : undefined,
    bindingObservationWindowMs: bindingSettlement.settled ? undefined : 3000,
  });
}

export default {
  async fetch(request: Request, env: ProbeEnv): Promise<Response> {
    if (env.APP_ENV !== 'staging') {
      return jsonResponse({ target: 'refused', status: 'not_staging' }, 503);
    }
    if (request.method !== 'POST') return new Response(null, { status: 405 });
    if (request.headers.get('X-Syrabit-Staging-Token') === null
      || !env.STAGING_ACCESS_TOKEN
      || !constantTimeEqual(
        request.headers.get('X-Syrabit-Staging-Token') ?? '',
        env.STAGING_ACCESS_TOKEN,
      )) {
      return new Response(null, { status: 401 });
    }

    const path = new URL(request.url).pathname;
    if (path === '/stream-stop') return runStreamingProbe(env);
    if (path === '/buffered-timeout') return runBufferedTimeoutProbe(env);
    return new Response(null, { status: 404 });
  },
};