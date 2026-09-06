import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

export const chatKeys = {
  sessions: ["chat", "sessions"] as const,
  messages: (sessionId: string) => ["chat", "sessions", sessionId, "messages"] as const,
};

export function useSessionsQuery() {
  return useQuery({
    queryKey: chatKeys.sessions,
    queryFn: () => window.docmind.chat.listSessions(),
  });
}

export function useMessagesQuery(sessionId: string | null) {
  return useQuery({
    queryKey: sessionId ? chatKeys.messages(sessionId) : ["chat", "messages"],
    queryFn: () => window.docmind.chat.listMessages(sessionId!),
    enabled: sessionId !== null,
  });
}

export function useCreateSessionMutation() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (repositoryIds: string[]) => window.docmind.chat.createSession({ repositoryIds }),
    onSuccess: async () => queryClient.invalidateQueries({ queryKey: chatKeys.sessions }),
  });
}

export function useEndSessionMutation() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (sessionId: string) => window.docmind.memory.endSession(sessionId),
    onSuccess: async () => queryClient.invalidateQueries({ queryKey: chatKeys.sessions }),
  });
}

export function useDeleteSessionMutation() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (sessionId: string) => window.docmind.memory.deleteSession(sessionId, true),
    onSuccess: async () => queryClient.invalidateQueries({ queryKey: chatKeys.sessions }),
  });
}
