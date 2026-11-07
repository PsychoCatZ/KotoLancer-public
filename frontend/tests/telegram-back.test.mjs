import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import vm from "node:vm";
import ts from "typescript";

// Exercise the SDK boundary without a Telegram session or access to a real account.
const source = readFileSync(
  new URL("../src/telegramBack.ts", import.meta.url),
  "utf8",
);
const compiled = ts.transpileModule(source, {
  compilerOptions: {
    module: ts.ModuleKind.CommonJS,
    target: ts.ScriptTarget.ES2022,
  },
}).outputText;
const callbacks = new Set();
let visible = false;
const exports = {};
vm.runInNewContext(compiled, {
  exports,
  window: {
    Telegram: {
      WebApp: {
        BackButton: {
          show() {
            visible = true;
          },
          hide() {
            visible = false;
          },
          onClick(fn) {
            callbacks.add(fn);
          },
          offClick(fn) {
            callbacks.delete(fn);
          },
        },
      },
    },
  },
});
const bind = exports.bindTelegramBack;
const presses = () => [...callbacks].forEach((callback) => callback());
let cards = 0,
  dialogs = 0;
const closeCard = bind(() => cards++);
assert.equal(visible, true);
presses();
assert.equal(cards, 1);
const closeDialog = bind(() => dialogs++, 100);
assert.equal(
  callbacks.size,
  1,
  "Only one native listener may dispatch a press",
);
presses();
assert.equal(dialogs, 1);
assert.equal(cards, 1, "Closing a form must not also leave its card");
closeDialog();
assert.equal(visible, true, "Card navigation survives dialog cleanup");
presses();
assert.equal(cards, 2);
closeCard();
assert.equal(visible, false);
assert.equal(
  callbacks.size,
  0,
  "Root screen leaves normal Telegram exit behavior",
);
const cleanup = bind(() => cards++);
cleanup();
const again = bind(() => cards++);
assert.equal(
  callbacks.size,
  1,
  "StrictMode remount must not duplicate listeners",
);
presses();
assert.equal(cards, 3);
again();
console.log(
  "Telegram back regression passed: card, dialog precedence, root and remount cleanup",
);
