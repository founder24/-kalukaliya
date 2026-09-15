import { useState } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import rehypeSanitize from 'rehype-sanitize';

function MarkdownImage({ alt = '', src, onError, ...props }) {
  const [imageFailed, setImageFailed] = useState(false);

  if (imageFailed) {
    return (
      <div
        role="alert"
        data-testid="markdown-image-fallback"
        className="my-4 flex min-h-24 items-center justify-center rounded-xl border border-amber-200 bg-amber-50 px-4 py-6 text-center text-sm text-amber-800"
      >
        {alt ? `${alt} image is unavailable.` : 'This page image is unavailable.'}
      </div>
    );
  }

  return (
    <img
      {...props}
      src={src}
      alt={alt}
      onError={(event) => {
        onError?.(event);
        setImageFailed(true);
      }}
    />
  );
}

export default function MarkdownRenderer({ children, components }) {
  return (
    <ReactMarkdown
      remarkPlugins={[remarkGfm]}
      rehypePlugins={[rehypeSanitize]}
      components={{ img: MarkdownImage, ...components }}
    >
      {children}
    </ReactMarkdown>
  );
}
