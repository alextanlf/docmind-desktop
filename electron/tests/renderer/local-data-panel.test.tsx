import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { appQueryClient } from "../../renderer/src/app/query-client";
import { LocalDataPanel } from "../../renderer/src/features/settings/LocalDataPanel";
import { installDocMindApi, readySettings } from "./test-docmind-api";

describe("本地数据面板", () => {
  beforeEach(() => {
    appQueryClient.clear();
  });

  it("把图片识别开关的当前状态显示出来", () => {
    installDocMindApi();

    render(<LocalDataPanel settings={{ ...readySettings, figureOcr: true }} />);

    expect(screen.getByRole("checkbox", { name: /识别图片里的文字/ })).toBeChecked();
    expect(screen.getByText("已开启")).toBeInTheDocument();
  });

  it("关掉开关会把设置写回后端", async () => {
    const setFigureOcr = vi.fn().mockResolvedValue({ ...readySettings, figureOcr: false });
    installDocMindApi({ settings: { setFigureOcr } });

    render(<LocalDataPanel settings={{ ...readySettings, figureOcr: true }} />);
    fireEvent.click(screen.getByRole("checkbox", { name: /识别图片里的文字/ }));

    await waitFor(() => expect(setFigureOcr).toHaveBeenCalledWith(false));
  });

  it("代价写清楚了 —— 关掉会丢什么必须让用户看得见", () => {
    installDocMindApi();

    render(<LocalDataPanel settings={readySettings} />);

    // 文案里必须提到"扫描"内容会失去检索能力。只写"更快/更慢"是不够的：用户
    // 无从知道关掉之后有整整一类文档会从索引里消失。
    expect(screen.getByText(/扫描.*无法检索|无法检索.*扫描/)).toBeInTheDocument();
  });
});

describe("图表语义描述开关", () => {
  beforeEach(() => {
    appQueryClient.clear();
  });

  it("默认关闭，并显示为已关闭", () => {
    installDocMindApi();

    render(<LocalDataPanel settings={{ ...readySettings, figureDescription: false }} />);

    expect(screen.getByRole("checkbox", { name: /用视觉模型描述图表/ })).not.toBeChecked();
  });

  it("把图片会被发出去这件事写清楚", () => {
    installDocMindApi();

    render(<LocalDataPanel settings={readySettings} />);

    // 这是唯一一处会把文档内容发到第三方的富化步骤，而且默认关闭。用户必须在打开之前
    // 就知道发的是什么 —— 只说"用模型看图"是不够的。
    expect(screen.getByText(/发送到.*模型服务商/)).toBeInTheDocument();
  });

  it("打开开关会写回后端", async () => {
    const setFigureDescription = vi
      .fn()
      .mockResolvedValue({ ...readySettings, figureDescription: true });
    installDocMindApi({ settings: { setFigureDescription } });

    render(<LocalDataPanel settings={{ ...readySettings, figureDescription: false }} />);
    fireEvent.click(screen.getByRole("checkbox", { name: /用视觉模型描述图表/ }));

    await waitFor(() => expect(setFigureDescription).toHaveBeenCalledWith(true));
  });
});
