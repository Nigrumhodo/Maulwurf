import { useTranslation } from "react-i18next";
import { Navigate, Route, Routes, useLocation, useParams } from "react-router-dom";
import { AuthProvider, RequireAuth, useAuth } from "@/components/auth/AuthProvider";
import { AppLayout } from "@/components/layout/AppLayout";
import { GestionLayout } from "@/components/layout/GestionLayout";
import { BlinkingCaret } from "@/components/motion/BlinkingCaret";
import { RetroVisual } from "@/components/ui/RetroVisual";
import { estadoError404, estadoError404Anim, estadoError404Claro } from "@/components/Visual";
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
import { PrivacyPage } from "@/pages/legal/PrivacyPage";
import { TermsPage } from "@/pages/legal/TermsPage";

function HomeRoute() {
  const { t } = useTranslation("common");
  const { user, loading, unavailable, retrySession } = useAuth();

  if (loading) {
    return (
      <div className="flex min-h-screen items-center justify-center">
        <p className="lowercase text-lavender">
          {t("app.loading")} <BlinkingCaret />
        </p>
      </div>
    );
  }

  if (unavailable) {
    return (
      <div className="flex min-h-screen flex-col items-center justify-center gap-4 px-4 text-center">
        <div className="border-2 border-pure-black bg-dark p-2 shadow-block">
          <RetroVisual
            src={estadoError404Anim}
            staticSrc={estadoError404}
            lightSrc={estadoError404Claro}
            className="h-32 w-32 object-contain"
          />
        </div>
        <p className="lowercase text-lavender">{t("app.sessionUnavailable")}</p>
        <button
          type="button"
          onClick={() => void retrySession()}
          className="lowercase font-pixel border-2 border-mint px-3 py-2 text-mint hover:bg-mint hover:text-on-accent"
        >
          {t("app.retry")}
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

// A12 — Legacy `/biblioteca/:audioId` deep links keep their query string (e.g. `?segment=`).
function RedirectBibliotecaDetail() {
  const { audioId = "" } = useParams();
  const { search } = useLocation();
  return <Navigate to={`/gestion/biblioteca/${encodeURIComponent(audioId)}${search}`} replace />;
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
        <Route path="/privacidad" element={<PrivacyPage />} />
        <Route path="/terminos" element={<TermsPage />} />

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
            <Route path="/biblioteca/:audioId" element={<RedirectBibliotecaDetail />} />
            <Route path="/chat" element={<Navigate to="/gestion/chat" replace />} />
            <Route path="/materias" element={<Navigate to="/gestion/materias" replace />} />
          </Route>
        </Route>

        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </AuthProvider>
  );
}
