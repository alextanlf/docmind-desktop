import { QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";
import { appQueryClient } from "./query-client";

export function AppProviders({ children }: { children: ReactNode }) {
  return <QueryClientProvider client={appQueryClient}>{children}</QueryClientProvider>;
}
