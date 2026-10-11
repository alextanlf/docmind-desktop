import type { BatchDiagnostic, CrawlRejectionReason } from "../../../../shared/contracts";
import { CrawlRejectionReasonSchema } from "../../../../shared/contracts";

/**
 * 每条拒绝原因的文案，以及**用户该怎么办**。
 *
 * 分开两部分是刻意的：`label` 说发生了什么，`hint` 说下一步做什么。只说"抓取失败"
 * 等于什么都没说 —— 用户既不知道是自己的问题还是站点的问题，也不知道该换来源、
 * 换请求，还是等一会儿重试。文案住在前端（后端只送原因码），与错误码文案同一个约定；
 * `REJECTION_COPY` 的类型是 `Record<CrawlRejectionReason, …>`，所以少写一条就编译不过。
 */
export interface RejectionCopy {
  label: string;
  hint: string;
}

export const REJECTION_COPY: Record<CrawlRejectionReason, RejectionCopy> = {
  robots_denied: {
    label: "站点 robots.txt 不允许抓取",
    hint: "这是站点的明确意愿，换个来源。",
  },
  scope_rejected: {
    label: "不在抓取范围内",
    hint: "只收录入口路径之下的页面；需要更外层的内容，就把入口换成那一级。",
  },
  unsupported_media: {
    label: "不是网页或 PDF",
    hint: "只收录 HTML / Markdown / PDF，其他文件请单独导入。",
  },
  limit_reached: {
    label: "等待过久，已跳过",
    hint: "该站点要求的长间隔超出本次预算；稍后重试这次导入即可。",
  },
  unreachable: {
    label: "连不上（DNS 解析失败或连接超时）",
    hint: "该站点在本地网络不可达，换镜像或换一个来源。",
  },
  throttled: {
    label: "被站点限流",
    hint: "站点在限速，隔一段时间再重试这次导入。",
  },
  http_rejected: {
    label: "站点拒绝了这次请求",
    hint: "多半是反爬或路径失效；换一个入口页面，或改用它的官方 API。",
  },
  geo_blocked: {
    label: "地域或法律限制（HTTP 451）",
    hint: "重试无用，只能换来源。",
  },
  tls_error: {
    label: "证书或握手失败",
    hint: "先看系统时间与证书链；要更快解决就换来源。",
  },
  proxy_error: {
    label: "代理不可用",
    hint: "检查代理设置 —— 这次失败不是站点的问题，换镜像解决不了。",
  },
  fetch_failed: {
    label: "抓取失败（没有记录到具体原因）",
    hint: "多为临时故障，重试这次导入。",
  },
};

export interface RejectionGroup extends RejectionCopy {
  reason: CrawlRejectionReason;
  urls: string[];
}

/**
 * 按原因分组，条数多的在前。
 *
 * 次级排序用**原因的声明顺序**而不是字典序：同一批数据每次渲染的分组顺序必须一致，
 * 否则用户展开一次、刷新一次，看到的排列都不一样。声明顺序是稳定的，字典序在加入
 * 新原因时会移动既有分组。
 */
export function groupRejections(items: readonly BatchDiagnostic[]): RejectionGroup[] {
  const declared = CrawlRejectionReasonSchema.options;
  const byReason = new Map<CrawlRejectionReason, string[]>();
  for (const item of items) {
    const urls = byReason.get(item.reason);
    if (urls) urls.push(item.canonicalUrl);
    else byReason.set(item.reason, [item.canonicalUrl]);
  }
  return [...byReason.entries()]
    .map(([reason, urls]) => ({ reason, urls, ...REJECTION_COPY[reason] }))
    .sort(
      (a, b) =>
        b.urls.length - a.urls.length || declared.indexOf(a.reason) - declared.indexOf(b.reason),
    );
}
