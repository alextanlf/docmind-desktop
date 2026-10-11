import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { AppProviders } from "../../renderer/src/app/AppProviders";
import { appQueryClient } from "../../renderer/src/app/query-client";
import { CorpusSettings } from "../../renderer/src/features/corpus/CorpusSettings";
import { installDocMindApi } from "./test-docmind-api";

/**
 * 网页语料页锁的是三件事：候选必须带证据、可信度必须看得见、以及收录/移除真的落库
 * （而不是只改了本地状态）。
 */
const SEED_CANDIDATE = {
  domain: "docs.python.org",
  signals: ["seed"],
  evidence: ["种子表（Python）：docs.python.org 是 python.org 的子域"],
  label: "Python 官方文档",
  kind: "official_docs",
  entryUrl: "https://docs.python.org/3/",
  verified: true,
  score: 40,
};

const GUESSED_CANDIDATE = {
  domain: "guessed.example",
  signals: ["nomination"],
  evidence: ["模型提名（未经核对）：某个文档站"],
  label: null,
  kind: null,
  entryUrl: null,
  verified: false,
  score: 20,
};

const COLLECTED = {
  domain: "docs.python.org",
  entryUrl: "https://docs.python.org/3/",
  label: "Python 官方文档",
  kind: "official_docs",
  verified: true,
  evidence: ["种子表（Python）：docs.python.org 是 python.org 的子域"],
  addedAt: "2026-10-11T00:00:00Z",
};

function renderPage() {
  return render(
    <AppProviders>
      <CorpusSettings />
    </AppProviders>,
  );
}

describe("网页语料", () => {
  beforeEach(() => {
    appQueryClient.clear();
  });

  it("推导候选时把证据和可信度都摆出来，收录后写回清单", async () => {
    const api = installDocMindApi({
      corpus: {
        candidates: vi.fn().mockResolvedValue({
          candidates: [SEED_CANDIDATE, GUESSED_CANDIDATE],
          nomination: { attempted: true, count: 1 },
        }),
        sources: vi.fn().mockResolvedValue({ sources: [] }),
        saveSources: vi.fn().mockResolvedValue({ sources: [COLLECTED] }),
      },
    });
    renderPage();

    fireEvent.change(screen.getByLabelText("问题或主题"), {
      target: { value: "Python 的 asyncio" },
    });
    fireEvent.click(screen.getByRole("button", { name: "推导来源" }));

    expect(await screen.findByText("docs.python.org")).toBeVisible();
    expect(api.corpus.candidates).toHaveBeenCalledWith({
      text: "Python 的 asyncio",
      limit: 20,
    });
    // 证据是这一页的重点：用户要能看出某个域名是谁提名的、凭什么。
    expect(screen.getByText(SEED_CANDIDATE.evidence[0])).toBeVisible();
    expect(screen.getByText(GUESSED_CANDIDATE.evidence[0])).toBeVisible();
    // 可信度必须看得见：种子表是核对过的，模型提名不是。
    expect(screen.getByText("已核对来源")).toBeVisible();
    expect(screen.getByText("未经核对")).toBeVisible();
    expect(screen.getByText("种子表")).toBeVisible();
    expect(screen.getByText("模型提名")).toBeVisible();

    fireEvent.click(screen.getByRole("button", { name: "收录 docs.python.org" }));

    await waitFor(() =>
      expect(api.corpus.saveSources).toHaveBeenCalledWith({
        sources: [
          {
            domain: "docs.python.org",
            entryUrl: "https://docs.python.org/3/",
            label: "Python 官方文档",
            kind: "official_docs",
            verified: true,
            evidence: [SEED_CANDIDATE.evidence[0]],
          },
        ],
      }),
    );
    // 响应直接写回缓存，所以按钮立刻变成"已收录"，而不是要用户自己刷新。
    expect(await screen.findByRole("button", { name: "已收录 docs.python.org" })).toBeDisabled();
  });

  it("模型给不出建议时如实说明，候选仍然来自种子表", async () => {
    installDocMindApi({
      corpus: {
        candidates: vi.fn().mockResolvedValue({
          candidates: [SEED_CANDIDATE],
          nomination: { attempted: true, count: 0 },
        }),
      },
    });
    renderPage();

    fireEvent.change(screen.getByLabelText("问题或主题"), { target: { value: "python" } });
    fireEvent.click(screen.getByRole("button", { name: "推导来源" }));

    expect(await screen.findByText(/模型这次没有给出建议/)).toBeVisible();
    expect(screen.getByText("docs.python.org")).toBeVisible();
  });

  it("列出的来源可以移除，移除后回到空状态", async () => {
    const api = installDocMindApi({
      corpus: {
        sources: vi.fn().mockResolvedValue({ sources: [COLLECTED] }),
        removeSource: vi.fn().mockResolvedValue({ sources: [] }),
      },
    });
    renderPage();

    expect(await screen.findByText("已收录的来源（1）")).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "移除 docs.python.org" }));

    await waitFor(() => expect(api.corpus.removeSource).toHaveBeenCalledWith("docs.python.org"));
    expect(await screen.findByText("还没有收录任何来源。")).toBeVisible();
  });

  it("抓取一个来源，显示本次结果与长期状态", async () => {
    const crawled = {
      ...COLLECTED,
      pageCount: 12,
      chunkCount: 48,
      lastCrawledAt: "2026-10-11T09:30:00Z",
      lastError: null,
    };
    const api = installDocMindApi({
      corpus: {
        sources: vi.fn().mockResolvedValue({ sources: [COLLECTED] }),
        crawlSource: vi.fn().mockResolvedValue({
          sources: [crawled],
          summary: {
            domain: "docs.python.org",
            pages: 12,
            chunks: 48,
            skippedCount: 3,
            truncated: true,
          },
        }),
      },
    });
    renderPage();

    expect(await screen.findByText("还没有抓取")).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "抓取 docs.python.org" }));

    await waitFor(() => expect(api.corpus.crawlSource).toHaveBeenCalledWith("docs.python.org"));
    // 本次结果与长期状态是两件事：前者是动作反馈，后者要留在列表上。
    expect(await screen.findByText(/本次抓到 12 页（48 段），跳过 3 个地址/)).toBeVisible();
    expect(screen.getByText(/已达到页数上限，这不是全部/)).toBeVisible();
    expect(await screen.findByText(/已收录 12 页 \/ 48 段/)).toBeVisible();
  });

  it("抓取一页都没进来时，用原因码对应的文案说清是什么问题", async () => {
    installDocMindApi({
      corpus: {
        sources: vi.fn().mockResolvedValue({
          sources: [{ ...COLLECTED, pageCount: 0, chunkCount: 0, lastError: "unreachable" }],
        }),
      },
    });
    renderPage();

    // 文案与批量导入的诊断共用同一张表 —— 原因码相同，说法就不该有两套。
    expect(
      await screen.findByText(/上次抓取没有收录任何页面：连不上（DNS 解析失败或连接超时）/),
    ).toBeVisible();
    expect(screen.getByText(/换镜像或换一个来源/)).toBeVisible();
  });

  it("手工添加把原样的输入交给后端规整，不在前端重复一套规则", async () => {
    const api = installDocMindApi({
      corpus: { saveSources: vi.fn().mockResolvedValue({ sources: [COLLECTED] }) },
    });
    renderPage();

    fireEvent.change(screen.getByLabelText("域名或网址"), {
      target: { value: "HTTPS://WWW.Example.com/guide" },
    });
    fireEvent.click(screen.getByRole("button", { name: "添加" }));

    await waitFor(() =>
      expect(api.corpus.saveSources).toHaveBeenCalledWith({
        sources: [
          {
            domain: "HTTPS://WWW.Example.com/guide",
            verified: false,
            evidence: ["你手工添加"],
          },
        ],
      }),
    );
  });
});
