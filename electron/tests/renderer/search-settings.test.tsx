import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { WebSearchSettings } from "../../renderer/src/features/settings/WebSearchSettings";
import { installDocMindApi, readySettings } from "./test-docmind-api";
describe("search settings", () => {
  it("renders three explicit policy modes without the saved key", () => {
    installDocMindApi();
    render(
      <QueryClientProvider client={new QueryClient()}>
        <WebSearchSettings settings={readySettings} />
      </QueryClientProvider>,
    );
    expect(screen.getByRole("radio", { name: "关闭" })).toBeDefined();
    expect(screen.getByRole("radio", { name: "询问" })).toBeDefined();
    expect(screen.getByRole("radio", { name: "自动" })).toBeDefined();
    expect(screen.queryByDisplayValue("secret")).toBeNull();
  });
});
