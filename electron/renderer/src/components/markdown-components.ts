import type { Components } from "react-markdown";
import { CodeBlock, InlineCode, MarkdownImage, MarkdownLink } from "./markdown-primitives";

/**
 * react-markdown 的组件映射表。单独成文件（非 .tsx 组件文件），
 * 避免 react-refresh 把常量导出当成组件文件报警告。
 */
export const markdownComponents: Components = {
  a: MarkdownLink,
  code: InlineCode,
  pre: CodeBlock,
  img: MarkdownImage,
};
