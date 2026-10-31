import { Navigate, Route, Routes } from "react-router-dom";
import { AuthProvider, RequireAuth } from "@/components/auth/AuthProvider";
import { AppLayout } from "@/components/layout/AppLayout";
import { GestionLayout } from "@/components/layout/GestionLayout";
import { LoginPage } from "@/pages/LoginPage";
import { RegisterPage } from "@/pages/RegisterPage";
import { WelcomePage } from "@/pages/WelcomePage";
import { DashboardPage } from "@/pages/DashboardPage";
import { BibliotecaPage } from "@/pages/BibliotecaPage";
import { ChatPage } from "@/pages/ChatPage";
import { MateriasPage } from "@/pages/MateriasPage";
import { TareasPage } from "@/pages/TareasPage";
import { AgendaPage } from "@/pages/AgendaPage";
import { SubirClasePage } from "@/pages/SubirClasePage";
import { GestionOverview } from "@/pages/GestionOverview";
import { SettingsPage } from "@/pages/SettingsPage";

// D1.2/D1.3 — Enrutado. `/` es el dashboard (protegido); la landing para
// visitantes es D1.8. Las opciones académicas viven bajo `/gestion` (menú
// lateral colapsable); `/biblioteca`, `/chat` y `/materias` redirigen ahí.
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
            <Route path="/settings" element={<SettingsPage />} />

            <Route path="/gestion" element={<GestionLayout />}>
              <Route index element={<GestionOverview />} />
              <Route path="subir" element={<SubirClasePage />} />
              <Route path="biblioteca" element={<BibliotecaPage />} />
              <Route path="chat" element={<ChatPage />} />
              <Route path="materias" element={<MateriasPage />} />
              <Route path="tareas" element={<TareasPage />} />
              <Route path="agenda" element={<AgendaPage />} />
            </Route>

            <Route path="/biblioteca" element={<Navigate to="/gestion/biblioteca" replace />} />
            <Route path="/chat" element={<Navigate to="/gestion/chat" replace />} />
            <Route path="/materias" element={<Navigate to="/gestion/materias" replace />} />
          </Route>
        </Route>

        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </AuthProvider>
  );
}
