/**
 * Structured Logger for PostHog and Sentry Integration
 */

interface LogEntry {
  timestamp: string;
  level: 'debug' | 'info' | 'warn' | 'error';
  message: string;
  context?: Record<string, unknown>;
  rayId?: string;
  userId?: string;
}

export class Logger {
  private serviceName: string;

  constructor(serviceName: string) {
    this.serviceName = serviceName;
  }

  private formatEntry(entry: LogEntry): string {
    return JSON.stringify({
      service: this.serviceName,
      ...entry,
    });
  }

  debug(message: string, context?: Record<string, unknown>): void {
    const entry: LogEntry = {
      timestamp: new Date().toISOString(),
      level: 'debug',
      message,
      context,
    };
    console.log(this.formatEntry(entry));
  }

  info(message: string, context?: Record<string, unknown>): void {
    const entry: LogEntry = {
      timestamp: new Date().toISOString(),
      level: 'info',
      message,
      context,
    };
    console.log(this.formatEntry(entry));
  }

  warn(message: string, context?: Record<string, unknown>): void {
    const entry: LogEntry = {
      timestamp: new Date().toISOString(),
      level: 'warn',
      message,
      context,
    };
    console.warn(this.formatEntry(entry));
  }

  error(message: string, error?: Error, context?: Record<string, unknown>): void {
    const entry: LogEntry = {
      timestamp: new Date().toISOString(),
      level: 'error',
      message,
      context: {
        ...context,
        error: error?.message,
        stack: error?.stack,
      },
    };
    console.error(this.formatEntry(entry));
  }
}

export const logger = new Logger('syrabit-edge');
