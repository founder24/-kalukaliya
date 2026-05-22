/**
 * Subject-specific Trustpilot review script generator.
 *
 * Produces a short, natural-sounding first-person draft that gives students
 * a personalised starting point before they open the Trustpilot form.
 * A warm, specific review is less likely to be filtered by Trustpilot's
 * fraud-detection than a short generic one.
 *
 * Task #155.
 */

const _SCIENCE_SUBJECTS = [
  'physics', 'chemistry', 'biology', 'botany', 'zoology',
  'biotechnology', 'microbiology', 'biochemistry',
];
const _MATHS_SUBJECTS = [
  'mathematics', 'maths', 'math', 'statistics', 'applied mathematics',
  'business mathematics', 'business maths',
];
const _HUMANITIES_SUBJECTS = [
  'history', 'political science', 'economics', 'geography',
  'philosophy', 'sociology', 'anthropology', 'education',
  'logic', 'psychology',
];
const _LANGUAGE_SUBJECTS = [
  'english', 'assamese', 'hindi', 'alternative english',
  'mil', 'modern indian language', 'bengali', 'bodo', 'manipuri',
  'urdu', 'nepali',
];

function _classifySubject(subjectName) {
  if (!subjectName) return 'general';
  const lower = subjectName.toLowerCase().trim();
  if (_MATHS_SUBJECTS.some((s) => lower.includes(s))) return 'maths';
  if (_SCIENCE_SUBJECTS.some((s) => lower.includes(s))) return 'science';
  if (_HUMANITIES_SUBJECTS.some((s) => lower.includes(s))) return 'humanities';
  if (_LANGUAGE_SUBJECTS.some((s) => lower.includes(s))) return 'language';
  return 'general';
}

function _contextLabel({ subjectName, boardName, className }) {
  const parts = [];
  if (boardName) parts.push(boardName);
  if (className) parts.push(className);
  if (subjectName) parts.push(subjectName);
  return parts.join(' ').trim();
}

const _TEMPLATES = {
  science({ subjectName, boardName, className }) {
    const ctx = _contextLabel({ subjectName, boardName, className });
    return (
      `I've been using Syrabit.ai to study ${ctx || 'my science subjects'} and it has genuinely changed how I prepare for exams. ` +
      `The chapter-by-chapter notes break down complex concepts into digestible explanations, and the AI tutor Syra gives clear answers grounded in the actual syllabus — no vague responses. ` +
      `The previous year questions with solutions helped me understand exactly what the board expects. ` +
      `Highly recommend it to anyone preparing for ${boardName || 'Assam Board'} science subjects.`
    );
  },

  maths({ subjectName, boardName, className }) {
    const ctx = _contextLabel({ subjectName, boardName, className });
    return (
      `Syrabit.ai made ${ctx || 'mathematics'} revision so much more manageable. ` +
      `The topic-wise breakdown is clean and the AI tutor explains working steps in a way that actually makes sense. ` +
      `Being able to ask Syra follow-up questions on a specific problem type — and get an answer aligned with the ${boardName || 'Assam Board'} syllabus — saved me hours of hunting through textbooks. ` +
      `Solid platform for anyone who finds maths challenging.`
    );
  },

  humanities({ subjectName, boardName, className }) {
    const ctx = _contextLabel({ subjectName, boardName, className });
    return (
      `I started using Syrabit.ai for ${ctx || 'my humanities subjects'} during exam season and it quickly became my go-to resource. ` +
      `The structured notes cover every chapter in the ${boardName || 'Assam Board'} syllabus, and important questions are clearly marked so I know what to focus on. ` +
      `The bilingual English/Assamese support is a huge plus — I can read explanations in whichever language helps me retain things better. ` +
      `Great tool for arts and humanities students.`
    );
  },

  language({ subjectName, boardName, className }) {
    const ctx = _contextLabel({ subjectName, boardName, className });
    return (
      `Syrabit.ai has been really helpful for my ${ctx || 'language paper'} preparation. ` +
      `The chapter summaries are concise and exam-focused, and I especially appreciated being able to ask Syra questions about grammar rules and writing formats specific to the ${boardName || 'Assam Board'} pattern. ` +
      `It saved me a lot of time I'd otherwise spend hunting through guides. ` +
      `Recommended for anyone studying language papers.`
    );
  },

  general({ boardName }) {
    return (
      `Syrabit.ai has been a genuinely useful addition to my study routine. ` +
      `The AI tutor gives syllabus-grounded answers rather than generic ones, the chapter notes are well-organised, and having previous year questions in one place makes revision far more efficient. ` +
      `I appreciate that I get 30 free AI credits every month without needing to sign up. ` +
      `If you're preparing for ${boardName || 'Assam Board'} exams, this platform is worth trying.`
    );
  },
};

/**
 * Build a personalised review draft for the given context.
 *
 * @param {object} opts
 * @param {string} [opts.subjectName]  e.g. "Physics"
 * @param {string} [opts.boardName]    e.g. "AHSEC"
 * @param {string} [opts.className]    e.g. "Class 12"
 * @returns {string}  A ready-to-edit first-person review draft.
 */
export function buildReviewScript({ subjectName = '', boardName = '', className = '' } = {}) {
  const type = _classifySubject(subjectName);
  const template = _TEMPLATES[type] || _TEMPLATES.general;
  return template({ subjectName, boardName, className }).trim();
}
