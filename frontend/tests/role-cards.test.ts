import { describe, expect, it } from "vitest";
import { blankCard, parseCard } from "../src/role-cards";

describe("Character Card V2 editor contract", () => {
  it("round-trips arbitrary extensions, nested lorebooks and optional-field presence", () => {
    const card = blankCard();
    card.data.name = "测试角色";
    card.data.description = "  中文\n\n保留空白\t";
    card.vendor = { nested: [true, 12, null, "保持"] };
    card.data.extensions = { "vendor/custom": { items: [1, false, ""] } };
    card.data.unknown = ["保存未知字段"];
    card.data.character_book = {
      extensions: {},
      entries: [
        {
          keys: ["城市"],
          content: "厦门",
          enabled: true,
          insertion_order: 0,
          extensions: { "vendor/data": "keep" },
          unknown: [1, 2],
        },
      ],
    };
    const result = parseCard(JSON.stringify(card));
    expect(result).toEqual(card);
    expect(result.data.character_book).not.toHaveProperty("description");
    result.data.name = "修改常规字段";
    expect(result.vendor).toEqual(card.vendor);
    expect(result.data.character_book).toEqual(card.data.character_book);
  });
  it.each(["{broken", '{"spec":"chara_card_v3"}', "null", "[]"])(
    "rejects malformed or unsupported input %s",
    (raw) => {
      expect(() => parseCard(raw)).toThrow();
    },
  );
  it("requires a name and typed V2 fields without coercion", () => {
    const card = blankCard();
    expect(() => parseCard(JSON.stringify(card))).toThrow("角色名称");
    card.data.name = "名称";
    const value = JSON.parse(JSON.stringify(card));
    value.data.tags = "not-an-array";
    expect(() => parseCard(JSON.stringify(value))).toThrow("tags");
    value.data.tags = [];
    value.data.description = 42;
    expect(() => parseCard(JSON.stringify(value))).toThrow("角色描述");
  });
  it("rejects malformed lorebook entries before leaving JSON mode", () => {
    const value = {
      ...blankCard(),
      data: {
        ...blankCard().data,
        name: "测试",
        character_book: {
          extensions: {},
          entries: [
            {
              keys: [],
              content: "",
              enabled: "false",
              insertion_order: 0,
              extensions: {},
            },
          ],
        },
      },
    };
    expect(() => parseCard(JSON.stringify(value))).toThrow("角色书条目");
  });
});
