import clsx from "clsx";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { markdownComponents } from "./markdown-components";

/**
 * 统一的 Markdown 渲染入口。聊天回答、引用面板和文档阅读视图共用同一套
 * 链接 / 代码块 / 图片处理，避免各feature 各写一份规则。
 */
export function MarkdownBody({ content, className }: { content: string; className?: string }) {
  return (
    <div className={clsx("markdown-body", className)}>
      <ReactMarkdown components={markdownComponents} remarkPlugins={[remarkGfm]}>
        {content}
      </ReactMarkdown>
    </div>
  );
}
