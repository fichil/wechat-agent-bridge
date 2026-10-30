import createClient from "openapi-fetch";
import type { components, paths } from "./schema";
import { authenticatedFetch } from "../auth";

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
export type CharacterCard = components["schemas"]["CharacterCard"];
export type RoleCard = components["schemas"]["RoleCard"];
export type CardSummary = components["schemas"]["CardSummary"];
export type ConversationPreferences =
  components["schemas"]["ConversationPreferences"];
export type PreferencesUpdate = components["schemas"]["PreferencesUpdate"];
export type ApiProfile = components["schemas"]["ApiProfile"];
export type ProfileCreate = components["schemas"]["ProfileCreate"];
export type ProfileUpdate = components["schemas"]["ProfileUpdate"];
export type AiDefaults = components["schemas"]["AiDefaults"];
export type DefaultsUpdate = components["schemas"]["DefaultsUpdate"];
export type AiSettings = components["schemas"]["AiSettings"];
export type AiRun = components["schemas"]["AiRun"];
export type RunList = components["schemas"]["RunList"];
export type ProfileTask = components["schemas"]["ProfileTask"];
export type Preset = components["schemas"]["Preset"];
export type LogSummary = components["schemas"]["LogSummary"];
export type LogDetail = components["schemas"]["LogDetail"];
export type LogQuery = components["schemas"]["LogQuery"];
export type SessionSummary = components["schemas"]["SessionSummary"];

export class ApiFailure extends Error {
  constructor(
    public code: string,
    message: string,
  ) {
    super(message);
  }
}

const client = createClient<paths>({
  fetch: authenticatedFetch,
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
  async sendCapability(account_id: string) {
    return value(
      await client.POST("/api/v1/wechat-send-status/query", {
        body: { account_id },
      }),
    );
  },
  async retryDelivery(
    account_id: string,
    conversation_id: string,
    run_id: string,
  ) {
    return value(
      await client.POST("/api/v1/ai-deliveries/retry", {
        body: { account_id, conversation_id, run_id },
      }),
    );
  },
  async resolveDelivery(
    account_id: string,
    conversation_id: string,
    run_id: string,
    resolution: "confirmed_sent" | "abandoned",
  ) {
    return value(
      await client.POST("/api/v1/ai-deliveries/resolve", {
        body: { account_id, conversation_id, run_id, resolution },
      }),
    );
  },
  async logs(body: LogQuery) {
    return value(await client.POST("/api/v1/logs/query", { body }));
  },
  async logDetail(account_id: string, log_id: string) {
    return value(
      await client.POST("/api/v1/logs/get", { body: { account_id, log_id } }),
    );
  },
  async clearLogs(account_id: string, scope: "account" | "global") {
    return value(
      await client.POST("/api/v1/logs/clear", { body: { account_id, scope } }),
    );
  },
  async aiSessions(account_id: string) {
    return value(
      await client.POST("/api/v1/ai-runs/sessions/query", {
        body: { account_id },
      }),
    );
  },
  async aiMessages(body: components["schemas"]["RunMessagesQuery"]) {
    return value(await client.POST("/api/v1/ai-runs/messages/query", { body }));
  },
  async aiSettings() {
    return value(await client.POST("/api/v1/ai-settings/query", { body: {} }));
  },
  async saveAiSettings(instructions: string, expected_revision: number) {
    return value(
      await client.POST("/api/v1/ai-settings/update", {
        body: { instructions, expected_revision },
      }),
    );
  },
  async profileTask(task_id: string) {
    return value(
      await client.POST("/api/v1/api-profiles/tasks/query", {
        body: { task_id },
      }),
    );
  },
  async testProfile(profile_id: string, expected_revision: number) {
    return value(
      await client.POST("/api/v1/api-profiles/test", {
        body: { profile_id, expected_revision },
      }),
    );
  },
  async aiRuns(account_id: string, conversation_id: string) {
    return value(
      await client.POST("/api/v1/ai-runs/list", {
        body: { account_id, conversation_id },
      }),
    );
  },
  async startAi(
    account_id: string,
    conversation_id: string,
    request_id: string,
  ) {
    return value(
      await client.POST("/api/v1/ai-runs/start", {
        body: { account_id, conversation_id, request_id },
      }),
    );
  },
  async retryAi(
    account_id: string,
    conversation_id: string,
    run_id: string,
    request_id: string,
  ) {
    return value(
      await client.POST("/api/v1/ai-runs/retry", {
        body: { account_id, conversation_id, run_id, request_id },
      }),
    );
  },
  async apiProfiles() {
    return value(await client.POST("/api/v1/api-profiles/query", { body: {} }));
  },
  async apiProfile(profile_id: string) {
    return value(
      await client.POST("/api/v1/api-profiles/get", { body: { profile_id } }),
    );
  },
  async createApiProfile(body: ProfileCreate) {
    return value(await client.POST("/api/v1/api-profiles/create", { body }));
  },
  async updateApiProfile(body: ProfileUpdate) {
    return value(await client.POST("/api/v1/api-profiles/update", { body }));
  },
  async deleteApiProfile(profile_id: string, expected_revision: number) {
    return value(
      await client.POST("/api/v1/api-profiles/delete", {
        body: { profile_id, expected_revision },
      }),
    );
  },
  async aiDefaults() {
    return value(await client.POST("/api/v1/ai-defaults/query", { body: {} }));
  },
  async saveAiDefaults(body: DefaultsUpdate) {
    return value(await client.POST("/api/v1/ai-defaults/update", { body }));
  },
  async roleCards() {
    return value(await client.POST("/api/v1/role-cards/query", { body: {} }));
  },
  async roleCard(card_code: string) {
    return value(
      await client.POST("/api/v1/role-cards/get", { body: { card_code } }),
    );
  },
  async createRoleCard(card: CharacterCard) {
    return value(
      await client.POST("/api/v1/role-cards/create", { body: { card } }),
    );
  },
  async updateRoleCard(
    card_code: string,
    expected_revision: number,
    card: CharacterCard,
  ) {
    return value(
      await client.POST("/api/v1/role-cards/update", {
        body: { card_code, expected_revision, card },
      }),
    );
  },
  async deleteRoleCard(card_code: string, expected_revision: number) {
    return value(
      await client.POST("/api/v1/role-cards/delete", {
        body: { card_code, expected_revision },
      }),
    );
  },
  async preferences(account_id: string, conversation_id: string) {
    return value(
      await client.POST("/api/v1/conversation-preferences/query", {
        body: { account_id, conversation_id },
      }),
    );
  },
  async savePreferences(body: PreferencesUpdate) {
    return value(
      await client.POST("/api/v1/conversation-preferences/update", { body }),
    );
  },
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
