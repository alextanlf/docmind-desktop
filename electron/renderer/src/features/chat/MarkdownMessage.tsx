import { Check, Copy } from "lucide-react";
import { useState, type ComponentPropsWithoutRef } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import type { Citation } from "../../../../shared/contracts";
import { CitationButton } from "../references/CitationButton";
import { openExternalUrl } from "../references/external-links";
import type { ScopedCitation } from "../references/citation-types";

type MarkdownNode = {
  type: string;
  value?: string;
  url?: string;
  children?: MarkdownNode[];
};

function citationRemarkPlugin(citations: ScopedCitation[]) {
  const citationsBySourceId = new Map(citations.map((item) => [item.citation.sourceId, item]));
  return () => (tree: MarkdownNode) => {
    const visit = (node: MarkdownNode) => {
      if (!node.children) return;
      node.children = node.children.flatMap((child) => {
        if (child.type !== "text" || !child.value) {
          visit(child);
          return child;
        }
        const parts: MarkdownNode[] = [];
        let lastIndex = 0;
        for (const match of child.value.matchAll(/\[([^\]\r\n]{1,255})\]/g)) {
          const sourceId = match[1];
          if (!citationsBySourceId.has(sourceId) || match.index === undefined) continue;
          if (match.index > lastIndex)
            parts.push({ type: "text", value: child.value.slice(lastIndex, match.index) });
          parts.push({
            type: "link",
            url: `https://docmind.local/citation/${encodeURIComponent(sourceId)}`,
            children: [{ type: "text", value: sourceId }],
          });
          lastIndex = match.index + match[0].length;
        }
        if (lastIndex === 0) return child;
        if (lastIndex < child.value.length)
          parts.push({ type: "text", value: child.value.slice(lastIndex) });
        return parts;
      });
    };
    visit(tree);
  };
}

function MarkdownLink({ href, children }: ComponentPropsWithoutRef<"a">) {
  if (href?.startsWith("https://docmind.local/citation/")) return null;
  if (!href || !/^https?:\/\//i.test(href)) return <span>{children}</span>;
  return (
    <a
      href={href}
      onClick={(event) => {
        event.preventDefault();
        openExternalUrl(href);
      }}
    >
      {children}
    </a>
  );
}

function CodeBlock({
  children,
  className,
  ...rest
}: ComponentPropsWithoutRef<"code"> & { inline?: boolean }) {
  const [copied, setCopied] = useState(false);
  const code = String(children).replace(/\n$/, "");
  const block = !rest.inline;
  if (!block) return <code className={className}>{children}</code>;
  return (
    <span className="markdown-code-block">
      <code className={className}>{code}</code>
      <button
        aria-label="复制代码"
        className="markdown-copy-button"
        onClick={() => {
          void navigator.clipboard?.writeText(code);
          setCopied(true);
        }}
        title={copied ? "已复制" : "复制代码"}
        type="button"
      >
        {copied ? <Check aria-hidden="true" size={15} /> : <Copy aria-hidden="true" size={15} />}
      </button>
    </span>
  );
}

function CitationLink({
  href,
  citationsBySourceId,
  children,
}: ComponentPropsWithoutRef<"a"> & { citationsBySourceId: Map<string, ScopedCitation> }) {
  if (href?.startsWith("https://docmind.local/citation/")) {
    const citation = citationsBySourceId.get(
      decodeURIComponent(href.slice("https://docmind.local/citation/".length)),
    );
    return citation ? <CitationButton scoped={citation} /> : <>{children}</>;
  }
  return <MarkdownLink href={href}>{children}</MarkdownLink>;
}

export function MarkdownMessage({
  content,
  citations,
  citationScope,
}: {
  content: string;
  citations: Citation[];
  citationScope?: string;
}) {
  const scoped = citations.map((citation) => ({
    id: `${citationScope ?? "message"}:${citation.sourceId}:${citation.chunkId}`,
    citation,
  }));
  const citationsBySourceId = new Map(scoped.map((item) => [item.citation.sourceId, item]));
  return (
    <div className="markdown-message">
      <ReactMarkdown
        components={{
          a: (props) => <CitationLink {...props} citationsBySourceId={citationsBySourceId} />,
          code: CodeBlock,
        }}
        remarkPlugins={[remarkGfm, citationRemarkPlugin(scoped)]}
      >
        {content}
      </ReactMarkdown>
    </div>
  );
}
