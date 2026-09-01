import { QueryClient } from "@tanstack/react-query";

type ClientError = { code?: string; retryable?: boolean };

const NON_RETRYABLE_CODE = /(AUTH|INVALID|VALIDATION|FORBIDDEN|UNAUTHORIZED|PRESET)/;

function canRetry(error: unknown): boolean {
  if (!error || typeof error !== "object") return false;
  const clientError = error as ClientError;
  return clientError.retryable === true && !NON_RETRYABLE_CODE.test(clientError.code ?? "");
}

export const appQueryClient = new QueryClient({
  defaultOptions: {
    queries: {
      retry: (failureCount, error) => failureCount < 1 && canRetry(error),
      retryDelay: 0,
      staleTime: 15_000,
      refetchOnWindowFocus: false,
    },
    mutations: { retry: false },
  },
});
