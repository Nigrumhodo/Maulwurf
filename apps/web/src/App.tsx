import { Navigate, Route, Routes } from "react-router-dom";
import { AuthProvider, RequireAuth, useAuth } from "@/components/auth/AuthProvider";
import { AppLayout } from "@/components/layout/AppLayout";
import { GestionLayout } from "@/components/layout/GestionLayout";
import { BlinkingCaret } from "@/components/motion/BlinkingCaret";
import { LandingPage } from "@/pages/LandingPage";
import { LoginPage } from "@/pages/LoginPage";
import { RegisterPage } from "@/pages/RegisterPage";
import { WelcomePage } from "@/pages/WelcomePage";
import { DashboardPage } from "@/pages/DashboardPage";
import { BibliotecaPage } from "@/pages/BibliotecaPage";
import { TranscriptReaderPage } from "@/pages/TranscriptReaderPage";
import { ChatPage } from "@/pages/ChatPage";
import { MateriasPage } from "@/pages/MateriasPage";
import { TareasPage } from "@/pages/TareasPage";
import { AgendaPage } from "@/pages/AgendaPage";
import { SubirClasePage } from "@/pages/SubirClasePage";
import { GestionOverview } from "@/pages/GestionOverview";
import { SettingsPage } from "@/pages/SettingsPage";

function HomeRoute() {
  const { user, loading, unavailable, retrySession } = useAuth();

  if (loading) {
    return (
      <div className="flex min-h-screen items-center justify-center">
        <p className="lowercase text-lavender">
          cargando <BlinkingCaret />
        </p>
      </div>
    );
  }

  if (unavailable) {
    return (
      <div className="flex min-h-screen flex-col items-center justify-center gap-4">
        <p className="lowercase text-lavender">no se pudo comprobar la sesión. revisa tu conexión.</p>
        <button
          type="button"
          onClick={() => void retrySession()}
          className="lowercase font-pixel border-2 border-mint px-3 py-2 text-mint hover:bg-mint hover:text-on-accent"
        >
          reintentar
        </button>
      </div>
    );
  }

  if (user === null) {
    return <LandingPage />;
  }

  return (
    <AppLayout>
      <DashboardPage />
    </AppLayout>
  );
}

// D1.2/D1.3/D1.8 — Enrutado. `/` muestra la landing pública cuando no hay sesión
// autenticada y el dashboard dentro de AppLayout cuando sí existe sesión.
export function App() {
  return (
    <AuthProvider>
      <Routes>
        <Route path="/" element={<HomeRoute />} />
        <Route path="/login" element={<LoginPage />} />
        <Route path="/register" element={<RegisterPage />} />
        <Route path="/welcome" element={<WelcomePage />} />

        <Route element={<RequireAuth />}>
          <Route element={<AppLayout />}>
            <Route path="/settings" element={<SettingsPage />} />

            <Route path="/gestion" element={<GestionLayout />}>
              <Route index element={<GestionOverview />} />
              <Route path="subir" element={<SubirClasePage />} />
              <Route path="biblioteca" element={<BibliotecaPage />} />
              <Route path="biblioteca/:audioId" element={<TranscriptReaderPage />} />
              <Route path="chat" element={<ChatPage />} />
              <Route path="materias" element={<MateriasPage />} />
              <Route path="tareas" element={<TareasPage />} />
              <Route path="agenda" element={<AgendaPage />} />
            </Route>

            <Route path="/biblioteca" element={<Navigate to="/gestion/biblioteca" replace />} />
            <Route path="/biblioteca/:audioId" element={<TranscriptReaderPage />} />
            <Route path="/chat" element={<Navigate to="/gestion/chat" replace />} />
            <Route path="/materias" element={<Navigate to="/gestion/materias" replace />} />
          </Route>
        </Route>

        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </AuthProvider>
  );
}
