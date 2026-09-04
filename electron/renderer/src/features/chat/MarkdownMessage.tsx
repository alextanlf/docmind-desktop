import { Check, Copy } from "lucide-react";
import {
  Children,
  isValidElement,
  useState,
  type ComponentPropsWithoutRef,
  type ReactNode,
} from "react";
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

const CITATION_URL_PREFIX = "https://docmind.local/citation/";

function citationRemarkPlugin(citations: ScopedCitation[]) {
  const citationsBySourceId = new Map(citations.map((item) => [item.citation.sourceId, item]));
  return () => (tree: MarkdownNode) => {
    let citationOccurrence = 0;
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
          const citationUrl = new URL(`${CITATION_URL_PREFIX}${encodeURIComponent(sourceId)}`);
          citationUrl.searchParams.set("occurrence", String(citationOccurrence));
          citationOccurrence += 1;
          parts.push({
            type: "link",
            url: citationUrl.toString(),
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
  if (href?.startsWith(CITATION_URL_PREFIX)) return null;
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

function codeText(children: ReactNode): string {
  return Children.toArray(children)
    .map((child) => {
      if (typeof child === "string") return child;
      if (isValidElement<{ children?: ReactNode }>(child)) return codeText(child.props.children);
      return "";
    })
    .join("")
    .replace(/\n$/, "");
}

function Code({ children, className, ...rest }: ComponentPropsWithoutRef<"code">) {
  return (
    <code className={className} {...rest}>
      {children}
    </code>
  );
}

function CodeBlock({ children, ...rest }: ComponentPropsWithoutRef<"pre">) {
  const [copied, setCopied] = useState(false);
  const code = codeText(children);
  return (
    <div className="markdown-code-block">
      <pre {...rest}>{children}</pre>
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
    </div>
  );
}

function CitationLink({
  href,
  citationsBySourceId,
  children,
}: ComponentPropsWithoutRef<"a"> & { citationsBySourceId: Map<string, ScopedCitation> }) {
  if (href?.startsWith(CITATION_URL_PREFIX)) {
    const citationUrl = new URL(href);
    const sourceId = decodeURIComponent(citationUrl.pathname.slice("/citation/".length));
    const citation = citationsBySourceId.get(sourceId);
    const occurrence = citationUrl.searchParams.get("occurrence") ?? "0";
    return citation ? (
      <CitationButton scoped={citation} triggerId={`${citation.id}:occurrence:${occurrence}`} />
    ) : (
      <>{children}</>
    );
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
    id: `${citationScope ?? "message"}:${citation.sourceId}:${citation.kind === "memory" ? citation.memoryId : citation.kind === "web" ? citation.resultId : citation.chunkId}`,
    citation,
  }));
  const citationsBySourceId = new Map(scoped.map((item) => [item.citation.sourceId, item]));
  return (
    <div className="markdown-message">
      <ReactMarkdown
        components={{
          a: (props) => <CitationLink {...props} citationsBySourceId={citationsBySourceId} />,
          code: Code,
          pre: CodeBlock,
        }}
        remarkPlugins={[remarkGfm, citationRemarkPlugin(scoped)]}
      >
        {content}
      </ReactMarkdown>
    </div>
  );
}
