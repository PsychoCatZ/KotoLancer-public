import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import vm from "node:vm";
import ts from "typescript";

const source = readFileSync(
  new URL("../src/orderLink.ts", import.meta.url),
  "utf8",
);
const compiled = ts.transpileModule(source, {
  compilerOptions: { module: ts.ModuleKind.CommonJS },
}).outputText;
const exports = {};
vm.runInNewContext(compiled, { exports, URL });
const parse = exports.orderFromUrl;
const withOrder = exports.urlWithOrder;
const id = "12345678-1234-1234-1234-123456789abc";
assert.equal(parse("https://example.test/?order=" + id), id);
assert.equal(parse("https://example.test/?a=1&order=" + id + "#card"), id);
assert.equal(
  withOrder("https://example.test/?keep=1&order=" + id + "#card", null),
  "https://example.test/?keep=1#card",
);
assert.equal(parse(withOrder("https://example.test/?keep=1", id)), id);
for (const query of [
  "",
  "?order=bad",
  "?order=",
  "?order=" + id + "&order=" + id,
  "?order=../etc",
]) {
  assert.equal(parse("https://example.test/" + query), null);
}
console.log(
  "Order links passed: exact card, normal URL parameters, invalid and ambiguous input.",
);
