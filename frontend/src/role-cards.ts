import type { CharacterCard } from "./api";

export const cardTextFields = [
  { key: "name", label: "角色名称", short: true },
  { key: "description", label: "角色描述" },
  { key: "personality", label: "性格" },
  { key: "scenario", label: "场景" },
  { key: "first_mes", label: "开场白" },
  { key: "mes_example", label: "示例对话" },
  { key: "system_prompt", label: "系统提示词" },
  { key: "post_history_instructions", label: "历史后指令" },
  { key: "creator_notes", label: "作者说明" },
  { key: "creator", label: "作者", short: true },
  { key: "character_version", label: "角色版本", short: true },
] as const;

export function blankCard(): CharacterCard {
  return {
    spec: "chara_card_v2",
    spec_version: "2.0",
    data: {
      name: "",
      description: "",
      personality: "",
      scenario: "",
      first_mes: "",
      mes_example: "",
      creator_notes: "",
      system_prompt: "",
      post_history_instructions: "",
      alternate_greetings: [],
      tags: [],
      creator: "",
      character_version: "",
      extensions: {},
    },
  };
}

function object(value: unknown): value is Record<string, unknown> {
  return !!value && typeof value === "object" && !Array.isArray(value);
}
function requireValue(ok: boolean, field: string): asserts ok {
  if (!ok) throw new Error(`角色卡格式不正确：${field}`);
}
function stringList(value: unknown) {
  return Array.isArray(value) && value.every((v) => typeof v === "string");
}
function optionalFields(
  value: Record<string, unknown>,
  fields: string[],
  type: string,
) {
  for (const key of fields) {
    if (value[key] !== undefined && value[key] !== null)
      requireValue(
        typeof value[key] === type &&
          (type !== "number" || Number.isFinite(value[key])),
        key,
      );
  }
}

// Validate the editable V2 fields, preserving all extension/unknown JSON data.
export function parseCard(text: string): CharacterCard {
  let card: unknown;
  try {
    card = JSON.parse(text);
  } catch {
    throw new Error("JSON 格式不正确，请检查引号、逗号和括号。");
  }
  requireValue(
    object(card) &&
      card.spec === "chara_card_v2" &&
      card.spec_version === "2.0",
    "仅支持 Character Card V2 / 2.0",
  );
  requireValue(object(card.data), "data");
  const data = card.data;
  for (const field of cardTextFields)
    requireValue(typeof data[field.key] === "string", field.label);
  requireValue(
    typeof data.name === "string" &&
      !!data.name.trim() &&
      Array.from(data.name).length <= 200,
    "角色名称须为 1–200 字",
  );
  requireValue(stringList(data.tags), "tags 须为字符串数组");
  requireValue(
    stringList(data.alternate_greetings),
    "alternate_greetings 须为字符串数组",
  );
  requireValue(object(data.extensions), "extensions 须为对象");
  if (data.character_book != null) {
    const book = data.character_book;
    requireValue(
      object(book) && object(book.extensions) && Array.isArray(book.entries),
      "character_book",
    );
    optionalFields(book, ["name", "description"], "string");
    optionalFields(book, ["scan_depth", "token_budget"], "number");
    optionalFields(book, ["recursive_scanning"], "boolean");
    for (const entry of book.entries as unknown[]) {
      requireValue(
        object(entry) &&
          stringList(entry.keys) &&
          typeof entry.content === "string" &&
          object(entry.extensions) &&
          typeof entry.enabled === "boolean" &&
          typeof entry.insertion_order === "number" &&
          Number.isFinite(entry.insertion_order),
        "角色书条目",
      );
      optionalFields(entry, ["name", "comment"], "string");
      optionalFields(entry, ["priority", "id"], "number");
      optionalFields(
        entry,
        ["case_sensitive", "selective", "constant"],
        "boolean",
      );
      if (entry.secondary_keys != null)
        requireValue(stringList(entry.secondary_keys), "secondary_keys");
      if (entry.position != null)
        requireValue(
          ["before_char", "after_char"].includes(String(entry.position)),
          "position",
        );
    }
  }
  return card as CharacterCard;
}

export function settingsError(cause: unknown) {
  return cause instanceof Error && cause.message
    ? cause.message
    : "本地服务暂不可用，请稍后重试。";
}
