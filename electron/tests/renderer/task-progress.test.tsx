import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { TaskProgress } from "../../renderer/src/components/TaskProgress";

describe("统一任务进度", () => {
  it("uses readable text when percentage is unavailable", () => {
    render(<TaskProgress title="qwen2.5:7b" progress={null} statusText="正在下载" />);
    expect(screen.getByText("进行中")).toBeInTheDocument();
    expect(screen.queryByRole("progressbar")).toBeNull();
  });

  it("exposes ARIA values and disables cancel while cancelling", () => {
    render(
      <TaskProgress
        title="导入文档"
        progress={37}
        progressText="37%"
        cancelPending
        onCancel={() => {}}
      />,
    );
    const bar = screen.getByRole("progressbar");
    expect(bar).toHaveAttribute("aria-valuemin", "0");
    expect(bar).toHaveAttribute("aria-valuemax", "100");
    expect(bar).toHaveAttribute("aria-valuenow", "37");
    expect(bar).toHaveAttribute("aria-valuetext", "37%");
    expect(screen.getByRole("button", { name: "取消导入文档" })).toBeDisabled();
  });
});
