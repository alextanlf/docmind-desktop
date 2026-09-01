import { QueryClient } from "@tanstack/react-query";

type ClientError = { code?: string; retryable?: boolean };

const NON_RETRYABLE_CODES = new Set([
  "DESTRUCTIVE_OPERATION",
  "FORBIDDEN",
  "INVALID_REQUEST",
  "MODEL_AUTH_FAILED",
  "MODEL_PRESET_INVALID",
  "MODEL_VALIDATION_FAILED",
  "UNAUTHORIZED",
  "VALIDATION_ERROR",
  "YUQUE_LOGIN_REQUIRED",
]);

function canRetry(error: unknown): boolean {
  if (!error || typeof error !== "object") return false;
  const clientError = error as ClientError;
  return clientError.retryable === true && !NON_RETRYABLE_CODES.has(clientError.code ?? "");
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
