import openapiTS, { astToString, COMMENT_HEADER } from "openapi-typescript";
import { readFile } from "node:fs/promises";

const schema = new URL("../../backend/openapi.json", import.meta.url);
const generated = COMMENT_HEADER + astToString(await openapiTS(schema));
const saved = await readFile(
  new URL("../src/api/schema.d.ts", import.meta.url),
  "utf8",
);
if (generated !== saved) {
  console.error("Frontend API types are stale; run npm run generate:api");
  process.exitCode = 1;
} else console.log("Frontend API types match OpenAPI");
