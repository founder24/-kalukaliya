import { RefreshCw, AlertTriangle, RotateCw } from 'lucide-react';
import { SectionErrorBoundary } from '@/components/ErrorBoundary';
import PrerenderStatusBody from './PrerenderStatusBody';

export default function PrerenderTab({ prerender, prerenderLoading, prerenderTriggering, loadPrerender, triggerPrerender }) {
  return (
          <SectionErrorBoundary name="Prerender Refresh" resetKeys={['prerender']}>
          <div className="space-y-4">
            <div className="rounded-2xl p-5 bg-white border border-gray-200 shadow-sm">
              <div className="flex items-start justify-between gap-3 mb-4">
                <div>
                  <h3 className="text-sm font-semibold text-gray-900 flex items-center gap-2">
                    <RotateCw size={14} className="text-violet-500" />
                    Cloudflare Pages prerender refresh
                  </h3>
                  <p className="text-xs text-gray-500 mt-1">
                    Rebuilds the prerendered subject &amp; chapter HTML so admin edits go live for crawlers and first paint.
                  </p>
                </div>
                <div className="flex items-center gap-2 shrink-0">
                  <button
                    onClick={loadPrerender}
                    disabled={prerenderLoading}
                    className="px-3 py-1.5 rounded-lg text-xs border border-gray-200 text-gray-500 hover:text-gray-700 disabled:opacity-50"
                    data-testid="button-prerender-reload"
                  >
                    <RefreshCw size={12} className={`inline mr-1 ${prerenderLoading ? 'animate-spin' : ''}`} />
                    Reload
                  </button>
                  <button
                    onClick={triggerPrerender}
                    disabled={prerenderTriggering}
                    className="px-3 py-1.5 rounded-lg text-xs font-semibold bg-violet-600 text-white hover:bg-violet-700 disabled:opacity-50"
                    data-testid="button-prerender-refresh-now"
                  >
                    <RotateCw size={12} className={`inline mr-1 ${prerenderTriggering ? 'animate-spin' : ''}`} />
                    {prerenderTriggering ? 'Queueing…' : 'Refresh now'}
                  </button>
                </div>
              </div>

              {prerenderLoading && !prerender ? (
                <div className="flex justify-center p-8">
                  <RefreshCw size={20} className="animate-spin text-gray-300" />
                </div>
              ) : prerender?._error ? (
                <div className="flex items-start gap-2 p-3 rounded-xl bg-red-50 border border-red-200 text-xs text-red-700">
                  <AlertTriangle size={14} className="mt-0.5 shrink-0" />
                  <span>{prerender._error}</span>
                </div>
              ) : prerender ? (
                <PrerenderStatusBody status={prerender} />
              ) : (
                <p className="text-xs text-gray-400">No status loaded.</p>
              )}
            </div>

            <p className="text-[11px] text-gray-400 leading-relaxed">
              Admin edits trigger debounced refreshes automatically. &quot;Refresh now&quot; bypasses the debounce/cooldown and fires the Cloudflare Pages deploy hook immediately.
            </p>
          </div>
          </SectionErrorBoundary>
  );
}
