import { Navigate, Route, Routes } from "react-router-dom";
import { LoginPage } from "@/pages/LoginPage";
import { RegisterPage } from "@/pages/RegisterPage";
import { WelcomePage } from "@/pages/WelcomePage";

// D1.2/D1.3 — Enrutado base. `/` (landing/dashboard) es D1.8 y de momento
// redirige a `/login`; las pantallas de auth ya usan el sistema retro.
export function App() {
  return (
    <Routes>
      <Route path="/" element={<Navigate to="/login" replace />} />
      <Route path="/login" element={<LoginPage />} />
      <Route path="/register" element={<RegisterPage />} />
      <Route path="/welcome" element={<WelcomePage />} />
      <Route path="*" element={<Navigate to="/login" replace />} />
    </Routes>
  );
}
