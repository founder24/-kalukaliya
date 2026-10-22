export function failOnMissingExport(warning, defaultHandler) {
  if (warning.code === 'MISSING_EXPORT') {
    throw new Error(
      `[vite] Missing named export (${warning.code}): ${warning.message}`,
    );
  }

  defaultHandler(warning);
}
