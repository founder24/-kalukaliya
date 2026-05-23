/**
 * Helpers for distinguishing DEGREE "course types" (Major/Minor/MDC/VAC/SEC/AEC)
 * from AHSEC/SEBA "streams" (Science, Arts, Commerce …).
 *
 * The database still stores both as `streams`, but the UI must surface the
 * correct label so DEGREE students see "Course Type" and school students see "Stream".
 */

const DEGREE_COURSE_SLUGS = new Set(['major', 'minor', 'mdc', 'vac', 'sec', 'aec']);

export const isDegreeBoard = (boardName) =>
  (boardName || '').trim().toUpperCase() === 'DEGREE';

const isDegreeStream = (streamName) =>
  DEGREE_COURSE_SLUGS.has((streamName || '').toLowerCase().trim());

/**
 * Returns the correct singular label for the "stream" concept.
 * @param {string} boardName  - board name from DB (e.g. "DEGREE", "AHSEC", "SEBA")
 * @param {string} [streamName] - optional stream name for heuristic fallback
 */
const streamLabel = (boardName, streamName = '') => {
  if (isDegreeBoard(boardName) || isDegreeStream(streamName)) return 'Course Type';
  return 'Stream';
};

/**
 * Returns a short tagline explaining what this step is about.
 */
export const streamStepHint = (boardName) => {
  if (isDegreeBoard(boardName)) return 'Which course type are you enrolled in?';
  return 'What stream are you studying?';
};
