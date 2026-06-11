import * as Sentry from "@sentry/react";
import { browserTracingIntegration } from "@sentry/react";

const dsn = import.meta.env.VITE_SENTRY_DSN;

if (dsn && import.meta.env.PROD) {
  Sentry.init({
    dsn,
    environment: import.meta.env.MODE || "production",
    release: import.meta.env.VITE_APP_VERSION,
    tracesSampleRate: 0.1,
    integrations: [browserTracingIntegration()],
    beforeSend(event) {
      const val = event.exception?.values?.[0]?.value || "";
      if (
        val.includes("Failed to fetch") ||
        val.includes("NetworkError") ||
        val.includes("Load failed")
      ) {
        return null;
      }
      return event;
    },
  });
}

export { Sentry };
