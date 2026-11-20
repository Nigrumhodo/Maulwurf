import type { Components } from "react-markdown";
import ReactMarkdown from "react-markdown";
import rehypeSanitize from "rehype-sanitize";
import remarkGfm from "remark-gfm";

const components: Components = {
  p: ({ children }) => <p className="my-0 leading-relaxed">{children}</p>,
  strong: ({ children }) => <strong className="font-bold">{children}</strong>,
  em: ({ children }) => <em className="italic">{children}</em>,
  del: ({ children }) => <del className="text-lavender/50 line-through">{children}</del>,
  h1: ({ children }) => <h3 className="mt-2 text-lg font-bold text-mint">{children}</h3>,
  h2: ({ children }) => <h4 className="mt-2 text-base font-bold text-mint">{children}</h4>,
  h3: ({ children }) => <h5 className="mt-2 text-base font-bold text-mint">{children}</h5>,
  h4: ({ children }) => <h6 className="mt-2 font-bold text-mint">{children}</h6>,
  h5: ({ children }) => <h6 className="mt-2 font-bold text-mint">{children}</h6>,
  h6: ({ children }) => <h6 className="mt-2 font-bold text-mint">{children}</h6>,
  ul: ({ children }) => <ul className="my-0 list-disc pl-5">{children}</ul>,
  ol: ({ children }) => <ol className="my-0 list-decimal pl-5">{children}</ol>,
  li: ({ children }) => <li className="my-0.5">{children}</li>,
  blockquote: ({ children }) => (
    <blockquote className="my-0 border-l-2 border-lavender/40 pl-3 text-lavender/80">
      {children}
    </blockquote>
  ),
  hr: () => <hr className="border-lavender/30" />,
  pre: ({ children }) => (
    <pre className="overflow-x-auto border-2 border-lavender/20 bg-surface px-3 py-2">
      {children}
    </pre>
  ),
  code: ({ children }) => <code className="font-code text-[0.9em] text-mint">{children}</code>,
  table: ({ children }) => (
    <div className="max-w-full overflow-x-auto">
      <table className="w-full border-collapse text-sm">{children}</table>
    </div>
  ),
  thead: ({ children }) => <thead className="text-mint">{children}</thead>,
  tbody: ({ children }) => <tbody>{children}</tbody>,
  tr: ({ children }) => <tr className="border-b border-lavender/20">{children}</tr>,
  th: ({ children }) => <th className="border border-lavender/30 px-2 py-1 text-left">{children}</th>,
  td: ({ children }) => <td className="border border-lavender/30 px-2 py-1 text-left">{children}</td>,
  a: ({ children }) => <span className="text-lavender/70">{children}</span>,
  img: () => null,
  input: () => null,
};

export interface MarkdownBodyProps {
  content: string;
}

/**
 * Markdown is rendered only for assistant text. React creates the elements; raw HTML is
 * skipped and URLs are neutralized. The validated citation list is the only navigation.
 */
export function MarkdownBody({ content }: MarkdownBodyProps) {
  return (
    <div className="normal-case flex flex-col gap-2">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        rehypePlugins={[rehypeSanitize]}
        skipHtml
        urlTransform={() => ""}
        components={components}
      >
        {content}
      </ReactMarkdown>
    </div>
  );
}
