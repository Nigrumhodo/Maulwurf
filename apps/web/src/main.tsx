import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import "./i18n";
import { App } from "./App";
import { GlitchBackground } from "./components/motion/GlitchBackground";
import { registerServiceWorker } from "./lib/pwa";
import { applyTheme, getStoredTheme } from "./lib/theme";
import "./index.css";

// Aplica el tema persistido antes del primer render para evitar parpadeo.
applyTheme(getStoredTheme());

if (import.meta.env.PROD) {
  void registerServiceWorker();
}

const container = document.getElementById("root");
if (container === null) {
  throw new Error("Falta el nodo #root en index.html");
}

createRoot(container).render(
  <StrictMode>
    <GlitchBackground />
    <BrowserRouter>
      <App />
    </BrowserRouter>
  </StrictMode>,
);
