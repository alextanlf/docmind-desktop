import { Check, Copy } from "lucide-react";
import {
  Children,
  isValidElement,
  useState,
  type ComponentPropsWithoutRef,
  type ReactNode,
} from "react";
import { openExternalUrl } from "../features/references/external-links";

/**
 * Markdown 渲染原语：链接走外部确认弹窗，代码块带复制按钮，图片只允许
 * http(s) 与内联 data URI（避免 file:// 之类的本地路径泄露到渲染层）。
 */

export function MarkdownLink({ href, children }: ComponentPropsWithoutRef<"a">) {
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

export function InlineCode({ children, className, ...rest }: ComponentPropsWithoutRef<"code">) {
  return (
    <code className={className} {...rest}>
      {children}
    </code>
  );
}

export function CodeBlock({ children, ...rest }: ComponentPropsWithoutRef<"pre">) {
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

export function MarkdownImage({ src, alt }: ComponentPropsWithoutRef<"img">) {
  if (!src || !/^(https?:\/\/|data:image\/)/i.test(src)) return <span>{alt ?? ""}</span>;
  return <img alt={alt ?? ""} loading="lazy" referrerPolicy="no-referrer" src={src} />;
}
