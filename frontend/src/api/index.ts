import createClient from "openapi-fetch";
import type { components, paths } from "./schema";

export type Account = components["schemas"]["Account"];
export type Snapshot = components["schemas"]["AccountSnapshot"];
export type ScanTask = components["schemas"]["ScanTask"];
export type Conversation = components["schemas"]["Conversation"];
export type ConversationPage = components["schemas"]["ConversationPage"];
export type ConversationRefresh = components["schemas"]["ConversationRefresh"];
export type Message = components["schemas"]["Message"];
export type MessagePage = components["schemas"]["MessagePage"];
export type MessageImage = components["schemas"]["MessageImage"];
export type MessageReadTask = components["schemas"]["MessageReadTask"];
export type Exclusions = components["schemas"]["Exclusions"];
export type ConversationSettings =
  components["schemas"]["ConversationSettings"];

export class ApiFailure extends Error {
  constructor(
    public code: string,
    message: string,
  ) {
    super(message);
  }
}

const client = createClient<paths>({
  fetch: (request) =>
    fetch(request, { signal: AbortSignal.timeout(15_000), cache: "no-store" }),
});

function value<T>(result: {
  data?: T;
  error?: unknown;
  response: Response;
}): T {
  if (result.data !== undefined) return result.data;
  const error = result.error as { code?: string; message?: string } | undefined;
  throw new ApiFailure(
    error?.code ?? "request_failed",
    error?.message ?? "请求失败，请重新识别",
  );
}

export const api = {
  async messages(
    accountId: string,
    conversationId: string,
    cursor: string | null = null,
  ): Promise<MessagePage> {
    return value(
      await client.POST("/api/v1/messages/query", {
        body: {
          account_id: accountId,
          conversation_id: conversationId,
          cursor,
          page_size: 50,
          refresh: false,
        },
      }),
    );
  },
  async cachedImage(
    accountId: string,
    conversationId: string,
    messageId: string,
  ): Promise<MessageImage> {
    return value(
      await client.POST("/api/v1/message-images/query", {
        body: {
          account_id: accountId,
          conversation_id: conversationId,
          message_id: messageId,
        },
      }),
    );
  },
  async readMessages(
    accountId: string,
    conversationId: string,
    cursor: string | null = null,
    refresh = false,
  ): Promise<MessageReadTask> {
    return value(
      await client.POST("/api/v1/message-reads", {
        body: {
          account_id: accountId,
          conversation_id: conversationId,
          cursor,
          page_size: 50,
          refresh,
        },
      }),
    );
  },
  async readMessageImage(
    accountId: string,
    conversationId: string,
    messageId: string,
  ): Promise<MessageReadTask> {
    return value(
      await client.POST("/api/v1/message-image-reads", {
        body: {
          account_id: accountId,
          conversation_id: conversationId,
          message_id: messageId,
        },
      }),
    );
  },
  async messageTask(
    accountId: string,
    taskId: string,
  ): Promise<MessageReadTask> {
    return value(
      await client.POST("/api/v1/message-read-tasks/query", {
        body: { account_id: accountId, task_id: taskId },
      }),
    );
  },
  async refreshConversations(accountId: string): Promise<ConversationRefresh> {
    return value(
      await client.POST("/api/v1/conversation-refreshes", {
        body: { account_id: accountId },
      }),
    );
  },
  async conversationRefresh(
    accountId: string,
    refreshId: string,
  ): Promise<ConversationRefresh> {
    return value(
      await client.POST("/api/v1/conversation-refreshes/query", {
        body: { account_id: accountId, refresh_id: refreshId },
      }),
    );
  },
  async conversations(
    accountId: string,
    cursor: string | null = null,
  ): Promise<ConversationPage> {
    return value(
      await client.POST("/api/v1/conversations/query", {
        body: {
          account_id: accountId,
          cursor,
          page_size: 50,
        } satisfies components["schemas"]["ConversationQuery"],
      }),
    );
  },
  async conversationSettings(accountId: string): Promise<ConversationSettings> {
    return value(
      await client.POST("/api/v1/conversation-settings/query", {
        body: { account_id: accountId },
      }),
    );
  },
  async saveConversationSettings(
    accountId: string,
    exclusions: Exclusions,
  ): Promise<ConversationSettings> {
    return value(
      await client.POST("/api/v1/conversation-settings/update", {
        body: { account_id: accountId, exclusions },
      }),
    );
  },
  async startScan(): Promise<ScanTask> {
    return value(await client.POST("/api/v1/account-scans", { body: {} }));
  },
  async scan(id: string): Promise<ScanTask> {
    return value(
      await client.POST("/api/v1/account-scans/query", {
        body: { scan_id: id },
      }),
    );
  },
  async accounts(): Promise<Snapshot> {
    return value(await client.POST("/api/v1/accounts/query", { body: {} }));
  },
  async account(id: string): Promise<Account> {
    return value(
      await client.POST("/api/v1/accounts/get", {
        body: { account_id: id },
      }),
    );
  },
};
