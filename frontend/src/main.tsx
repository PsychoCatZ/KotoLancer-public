import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import App from "./App";
import "./style.css";
import "./dark.css";
import { prepareTelegram } from "./telegram";
import { applyTheme, readTheme } from "./theme";

applyTheme(readTheme());

void prepareTelegram().finally(() => {
  applyTheme(readTheme());
  createRoot(document.getElementById("root")!).render(
    <StrictMode>
      <App />
    </StrictMode>,
  );
});
