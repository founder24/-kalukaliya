/**
 * bedrock-proxy — SigV4 proxy Worker for AWS Polly, Transcribe, and Translate.
 *
 * The Cloudflare AI Gateway aws-bedrock BYOK slug covers Bedrock Converse
 * (LLM) and Titan Embeddings, but does NOT proxy Amazon Polly (TTS),
 * Amazon Transcribe (STT), or Amazon Translate.  This Worker signs requests
 * with AWS SigV4 and forwards them to the appropriate AWS service endpoints.
 *
 * Routes:
 *   POST /polly/synthesize   — Amazon Polly SynthesizeSpeech
 *   POST /transcribe         — Amazon Transcribe (StartTranscriptionJob + poll)
 *   POST /translate          — Amazon Translate TranslateText
 *   GET  /health             — Liveness check
 *
 * Required secrets (set via `wrangler secret put`):
 *   AWS_ACCESS_KEY_ID
 *   AWS_SECRET_ACCESS_KEY
 *   AWS_REGION              (optional, defaults to "us-east-1")
 *   BEDROCK_PROXY_AUTH_TOKEN (optional shared auth secret)
 *   AWS_S3_BUCKET           (required for /transcribe)
 *
 * The backend (providers/bedrock.py) must set BEDROCK_PROXY_URL to the
 * deployed Worker URL and optionally pass Authorization: Bearer <token>.
 */

import { AwsClient } from 'aws4fetch';

export interface Env {
  AWS_ACCESS_KEY_ID: string;
  AWS_SECRET_ACCESS_KEY: string;
  AWS_REGION?: string;
  BEDROCK_PROXY_AUTH_TOKEN?: string;
  AWS_S3_BUCKET?: string;
}

export default {
  async fetch(request: Request, env: Env): Promise<Response> {
    const url = new URL(request.url);
    const path = url.pathname;

    // Health check — no auth required.
    if (request.method === 'GET' && path === '/health') {
      return Response.json({ ok: true, worker: 'bedrock-proxy' });
    }

    // Authenticate — optional shared secret.
    if (env.BEDROCK_PROXY_AUTH_TOKEN) {
      const authHeader = request.headers.get('Authorization') ?? '';
      const token = authHeader.startsWith('Bearer ') ? authHeader.slice(7) : '';
      if (token !== env.BEDROCK_PROXY_AUTH_TOKEN) {
        return new Response('Unauthorized', { status: 401 });
      }
    }

    if (request.method !== 'POST') {
      return new Response('Method Not Allowed', { status: 405 });
    }

    const region = env.AWS_REGION ?? 'us-east-1';

    const aws = new AwsClient({
      accessKeyId: env.AWS_ACCESS_KEY_ID,
      secretAccessKey: env.AWS_SECRET_ACCESS_KEY,
      region,
    });

    let body: Record<string, unknown>;
    try {
      body = await request.json() as Record<string, unknown>;
    } catch {
      return new Response('Invalid JSON body', { status: 400 });
    }

    // ── Amazon Polly TTS ────────────────────────────────────────────────────
    if (path === '/polly/synthesize') {
      const pollyUrl = `https://polly.${region}.amazonaws.com/v1/speech`;
      const payload = JSON.stringify({
        Text: String(body.text ?? ''),
        VoiceId: String(body.voice_id ?? 'Raveena'),
        OutputFormat: String(body.output_format ?? 'mp3'),
        Engine: 'neural',
        TextType: 'text',
      });

      let awsResp: Response;
      try {
        awsResp = await aws.fetch(pollyUrl, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: payload,
        });
      } catch (err) {
        return new Response(`Polly connection error: ${String(err)}`, { status: 502 });
      }

      if (!awsResp.ok) {
        const errText = await awsResp.text();
        return new Response(errText, { status: awsResp.status });
      }
      // Stream audio bytes back to caller.
      return new Response(awsResp.body, {
        status: 200,
        headers: {
          'Content-Type': 'audio/mpeg',
          'Cache-Control': 'public, max-age=3600',
        },
      });
    }

    // ── Amazon Translate ────────────────────────────────────────────────────
    if (path === '/translate') {
      const translateUrl = `https://translate.${region}.amazonaws.com/`;
      const payload = JSON.stringify({
        Text: String(body.text ?? ''),
        SourceLanguageCode: String(body.source_language_code ?? 'en'),
        TargetLanguageCode: String(body.target_language_code ?? 'hi'),
      });

      let awsResp: Response;
      try {
        awsResp = await aws.fetch(translateUrl, {
          method: 'POST',
          headers: {
            'Content-Type': 'application/x-amz-json-1.1',
            'X-Amz-Target': 'AWSShineFrontendService_20170701.TranslateText',
          },
          body: payload,
        });
      } catch (err) {
        return new Response(`Translate connection error: ${String(err)}`, { status: 502 });
      }

      if (!awsResp.ok) {
        const errText = await awsResp.text();
        return new Response(errText, { status: awsResp.status });
      }

      const result = await awsResp.json() as { TranslatedText: string };
      return Response.json({ translated_text: result.TranslatedText ?? '' });
    }

    // ── Amazon Transcribe (batch via S3) ────────────────────────────────────
    if (path === '/transcribe') {
      const s3Bucket = env.AWS_S3_BUCKET;
      if (!s3Bucket) {
        return new Response(
          JSON.stringify({ error: 'AWS_S3_BUCKET not configured — required for Transcribe' }),
          { status: 503, headers: { 'Content-Type': 'application/json' } },
        );
      }

      const audiob64 = String(body.audio_b64 ?? '');
      const mimeType = String(body.mime_type ?? 'audio/wav');
      const languageCode = String(body.language_code ?? 'en-US');

      // Decode base64 audio.
      let audioData: Uint8Array;
      try {
        const binaryStr = atob(audiob64);
        audioData = new Uint8Array(binaryStr.length);
        for (let i = 0; i < binaryStr.length; i++) {
          audioData[i] = binaryStr.charCodeAt(i);
        }
      } catch {
        return new Response('Invalid audio_b64 (base64 decode failed)', { status: 400 });
      }

      // Derive file extension from mime type.
      const extMap: Record<string, string> = {
        'audio/wav': 'wav',
        'audio/wave': 'wav',
        'audio/mpeg': 'mp3',
        'audio/mp3': 'mp3',
        'audio/mp4': 'mp4',
        'audio/webm': 'webm',
        'audio/flac': 'flac',
        'audio/ogg': 'ogg',
      };
      const ext = extMap[mimeType] ?? 'wav';
      const jobName = `syrabit-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
      const s3Key = `transcribe-input/${jobName}.${ext}`;
      const s3Uri = `s3://${s3Bucket}/${s3Key}`;

      // ── Step 1: Upload audio to S3 ────────────────────────────────────────
      const s3UploadUrl = `https://${s3Bucket}.s3.${region}.amazonaws.com/${s3Key}`;
      try {
        const uploadResp = await aws.fetch(s3UploadUrl, {
          method: 'PUT',
          headers: { 'Content-Type': mimeType },
          body: audioData,
        });
        if (!uploadResp.ok) {
          const errText = await uploadResp.text();
          return new Response(`S3 upload failed: ${errText}`, { status: uploadResp.status });
        }
      } catch (err) {
        return new Response(`S3 connection error: ${String(err)}`, { status: 502 });
      }

      // ── Step 2: Start Transcription Job ───────────────────────────────────
      const transcribeUrl = `https://transcribe.${region}.amazonaws.com/`;
      const startPayload = JSON.stringify({
        TranscriptionJobName: jobName,
        LanguageCode: languageCode,
        MediaFormat: ext,
        Media: { MediaFileUri: s3Uri },
        OutputBucketName: s3Bucket,
        OutputKey: `transcribe-output/${jobName}.json`,
      });

      try {
        const startResp = await aws.fetch(transcribeUrl, {
          method: 'POST',
          headers: {
            'Content-Type': 'application/x-amz-json-1.1',
            'X-Amz-Target': 'Transcribe.StartTranscriptionJob',
          },
          body: startPayload,
        });
        if (!startResp.ok) {
          const errText = await startResp.text();
          return new Response(`Transcribe start failed: ${errText}`, { status: startResp.status });
        }
      } catch (err) {
        return new Response(`Transcribe start error: ${String(err)}`, { status: 502 });
      }

      // ── Step 3: Poll for completion (max 25s, 2s interval) ────────────────
      const getPayload = JSON.stringify({ TranscriptionJobName: jobName });
      let transcript = '';
      let attempts = 0;
      const maxAttempts = 12;

      while (attempts < maxAttempts) {
        await new Promise(r => setTimeout(r, 2000));
        attempts++;

        let getResp: Response;
        try {
          getResp = await aws.fetch(transcribeUrl, {
            method: 'POST',
            headers: {
              'Content-Type': 'application/x-amz-json-1.1',
              'X-Amz-Target': 'Transcribe.GetTranscriptionJob',
            },
            body: getPayload,
          });
        } catch (err) {
          continue;
        }

        if (!getResp.ok) continue;

        const jobData = await getResp.json() as {
          TranscriptionJob?: {
            TranscriptionJobStatus: string;
            Transcript?: { TranscriptFileUri?: string };
          };
        };

        const status = jobData?.TranscriptionJob?.TranscriptionJobStatus ?? '';
        if (status === 'COMPLETED') {
          const transcriptUri = jobData?.TranscriptionJob?.Transcript?.TranscriptFileUri ?? '';
          if (transcriptUri) {
            try {
              const transcriptResp = await fetch(transcriptUri);
              const transcriptData = await transcriptResp.json() as {
                results?: { transcripts?: Array<{ transcript: string }> };
              };
              transcript = transcriptData?.results?.transcripts?.[0]?.transcript ?? '';
            } catch {
              transcript = '';
            }
          }
          break;
        } else if (status === 'FAILED') {
          return new Response(
            JSON.stringify({ error: 'Transcription job failed' }),
            { status: 502, headers: { 'Content-Type': 'application/json' } },
          );
        }
      }

      return Response.json({ transcript });
    }

    return new Response('Not Found', { status: 404 });
  },
} satisfies ExportedHandler<Env>;
