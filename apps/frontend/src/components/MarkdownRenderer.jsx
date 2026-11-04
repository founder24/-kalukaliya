import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import rehypeSanitize from 'rehype-sanitize';
import { defaultSchema } from 'rehype-sanitize';
import remarkMath from 'remark-math';
import rehypeKatex from 'rehype-katex';
import 'katex/dist/katex.min.css';
import './studyMarkdown.css';
import { normalizeStudyMarkdown } from '@/utils/normalizeStudyMarkdown';

const mathSchema = {
  ...defaultSchema,
  attributes: {
    ...defaultSchema.attributes,
    span: [...(defaultSchema.attributes?.span || []), ['className', 'math', 'math-inline']],
    div: [...(defaultSchema.attributes?.div || []), ['className', 'math', 'math-display']],
    code: [...(defaultSchema.attributes?.code || []),
      ['className', /^language-./, 'math-inline', 'math-display']],
  },
};

export default function MarkdownRenderer({ children, components }) {
  return (
    <ReactMarkdown
      className="study-markdown"
      remarkPlugins={[remarkGfm, remarkMath]}
      rehypePlugins={[[rehypeSanitize, mathSchema], [rehypeKatex, { trust: false, strict: 'ignore' }]]}
      components={components}
    >
      {normalizeStudyMarkdown(children)}
    </ReactMarkdown>
  );
}
