import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import { App } from "./App";
import { GlitchBackground } from "./components/motion/GlitchBackground";
import { applyTheme, getStoredTheme } from "./lib/theme";
import "./index.css";

// Aplica el tema persistido antes del primer render para evitar parpadeo.
applyTheme(getStoredTheme());

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
