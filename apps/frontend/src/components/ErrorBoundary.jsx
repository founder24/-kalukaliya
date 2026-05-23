/**
 * ErrorBoundary — Syrabit.ai
 * Catches JS errors in child component tree.
 * Provides user-friendly fallback + reports to window.Sentry / PostHog.
 */
import { Component } from 'react';
import { RefreshCw, Home, AlertTriangle } from 'lucide-react';
import { log } from '@/utils/logger';

export class ErrorBoundary extends Component {
  constructor(props) {
    super(props);
    this.state = { hasError: false, error: null, errorInfo: null };
  }

  static getDerivedStateFromError(error) {
    return { hasError: true, error };
  }

  componentDidCatch(error, errorInfo) {
    this.setState({ errorInfo });

    // Report to Sentry if available
    if (window.Sentry) {
      window.Sentry.captureException(error, { extra: errorInfo });
    }

    // Report to PostHog if available
    if (window.posthog) {
      window.posthog.capture('error_boundary_triggered', {
        error_message: error.message,
        component_stack: errorInfo.componentStack,
        page: window.location.pathname,
      });
    }

    log.error('[ErrorBoundary] Uncaught render error', {
      error: error.message,
      stack: error.stack?.slice(0, 500),
      page: window.location.pathname,
      componentStack: errorInfo?.componentStack?.slice(0, 400),
    });
  }

  handleReset = () => {
    this.setState({ hasError: false, error: null, errorInfo: null });
  };

  render() {
    if (!this.state.hasError) {
      return this.props.children;
    }

    return (
      <div
        className="min-h-screen flex items-center justify-center bg-background"
        role="alert"
        aria-live="assertive"
      >
        <div className="max-w-md w-full mx-4 text-center">
          <div className="flex justify-center mb-6">
            <div
              className="w-20 h-20 rounded-3xl flex items-center justify-center"
              style={{
                background: 'linear-gradient(135deg, rgba(239,68,68,0.20), rgba(220,38,38,0.12))',
                border: '1px solid rgba(239,68,68,0.30)',
                boxShadow: '0 0 30px rgba(239,68,68,0.15)',
              }}
            >
              <AlertTriangle size={36} className="text-destructive" aria-hidden="true" />
            </div>
          </div>

          <h1 className="text-2xl font-bold text-foreground mb-2">Something went wrong</h1>
          <p className="text-muted-foreground text-sm mb-6">
            Syra encountered an unexpected error. Our team has been notified.
          </p>

          {/* Error details (dev only) */}
          {process.env.NODE_ENV !== 'production' && this.state.error && (
            <details className="text-left mb-6 rounded-xl overflow-hidden" style={{ background: 'rgba(239,68,68,0.06)', border: '1px solid rgba(239,68,68,0.15)' }}>
              <summary className="px-4 py-3 text-xs font-semibold text-destructive cursor-pointer">
                Error Details (dev only)
              </summary>
              <pre className="px-4 pb-4 text-xs text-muted-foreground overflow-x-auto">
                {this.state.error.message}\n{this.state.errorInfo?.componentStack}
              </pre>
            </details>
          )}

          <div className="flex gap-3 justify-center">
            <button
              onClick={() => window.location.reload()}
              className="flex items-center gap-2 px-4 py-2 rounded-xl text-sm font-semibold text-muted-foreground border border-border hover:bg-muted/40 transition-colors"
              aria-label="Refresh the page"
            >
              <RefreshCw size={16} aria-hidden="true" /> Refresh
            </button>
            <button
              onClick={() => { window.location.href = '/'; this.handleReset(); }}
              className="flex items-center gap-2 px-4 py-2 rounded-xl text-sm font-semibold text-white transition-all hover:opacity-90"
              style={{ background: 'linear-gradient(135deg, #7c3aed, #8b5cf6)' }}
              aria-label="Go to home page"
            >
              <Home size={16} aria-hidden="true" /> Go Home
            </button>
          </div>
        </div>
      </div>
    );
  }
}

const RouteErrorBoundary = ({ children }) => (
  <ErrorBoundary>{children}</ErrorBoundary>
);

// Shallow-equality check for resetKeys arrays. If the list of values
// passed by the parent changes between renders, we drop any tripped
// fallback so a successful re-fetch (or a sub-page switch) clears the
// boundary without forcing the user to click "Try again" on every card.
function resetKeysChanged(prev, next) {
  if (prev === next) return false;
  if (!Array.isArray(prev) || !Array.isArray(next)) return true;
  if (prev.length !== next.length) return true;
  for (let i = 0; i < prev.length; i += 1) {
    if (!Object.is(prev[i], next[i])) return true;
  }
  return false;
}

export class SectionErrorBoundary extends Component {
  constructor(props) {
    super(props);
    this.state = { hasError: false, error: null, prevResetKeys: props.resetKeys };
  }

  static getDerivedStateFromError(error) {
    return { hasError: true, error };
  }

  static getDerivedStateFromProps(props, state) {
    if (resetKeysChanged(state.prevResetKeys, props.resetKeys)) {
      return { hasError: false, error: null, prevResetKeys: props.resetKeys };
    }
    return null;
  }

  componentDidCatch(error, errorInfo) {
    log.error('[SectionErrorBoundary] Widget crash', {
      section: this.props.name || 'unknown',
      error: error.message,
    });
  }

  render() {
    if (!this.state.hasError) return this.props.children;
    return (
      <div className="rounded-xl border border-red-500/20 bg-red-500/5 p-4 text-center" role="alert">
        <AlertTriangle size={20} className="text-red-400 mx-auto mb-2" />
        <p className="text-sm text-red-300 font-medium mb-1">
          {this.props.name || 'Section'} failed to load
        </p>
        <button
          onClick={() => this.setState({ hasError: false, error: null })}
          className="text-xs text-violet-400 hover:text-violet-300 underline"
        >
          Try again
        </button>
      </div>
    );
  }
}
