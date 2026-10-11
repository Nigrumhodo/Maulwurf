import { Navigate, Route, Routes } from "react-router-dom";
import { AuthProvider, RequireAuth } from "@/components/auth/AuthProvider";
import { AppLayout } from "@/components/layout/AppLayout";
import { LoginPage } from "@/pages/LoginPage";
import { RegisterPage } from "@/pages/RegisterPage";
import { WelcomePage } from "@/pages/WelcomePage";
import { DashboardPage } from "@/pages/DashboardPage";
import { BibliotecaPage } from "@/pages/BibliotecaPage";
import { ChatPage } from "@/pages/ChatPage";
import { MateriasPage } from "@/pages/MateriasPage";
import { SettingsPage } from "@/pages/SettingsPage";

// D1.2/D1.3 — Enrutado. `/` es el dashboard (protegido); la landing para
// visitantes es D1.8. Sin sesión, las rutas protegidas redirigen a `/login`.
export function App() {
  return (
    <AuthProvider>
      <Routes>
        <Route path="/login" element={<LoginPage />} />
        <Route path="/register" element={<RegisterPage />} />
        <Route path="/welcome" element={<WelcomePage />} />

        <Route element={<RequireAuth />}>
          <Route element={<AppLayout />}>
            <Route path="/" element={<DashboardPage />} />
            <Route path="/biblioteca" element={<BibliotecaPage />} />
            <Route path="/chat" element={<ChatPage />} />
            <Route path="/materias" element={<MateriasPage />} />
            <Route path="/settings" element={<SettingsPage />} />
          </Route>
        </Route>

        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </AuthProvider>
  );
}
