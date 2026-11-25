import { createContext, useCallback, useContext, useEffect, useRef, useState } from "react";
import type { ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { Navigate, Outlet, useNavigate } from "react-router-dom";
import { fetchMe, postLogout } from "@/lib/auth";
import type { Me } from "@/lib/auth";
import { configureApiHandlers } from "@/lib/api";
import { clearCsrfToken } from "@/lib/csrf";
import { BlinkingCaret } from "@/components/motion/BlinkingCaret";

// D1.2/D1.4 — Estado de sesión compartido. `GET /me` decide si hay sesión (cookie
// `mw_session`); sin sesión el backend responde 401 y aquí se queda `user=null`.
// El token CSRF se mantiene solo en memoria (`lib/csrf.ts`), nunca en storage
// (regla D1.4/D2.8).

interface AuthContextValue {
  user: Me | null;
  loading: boolean;
  /** `GET /me` failed for a reason other than 401. */
  unavailable: boolean;
  retrySession: () => Promise<void>;
  logout: () => Promise<void>;
}

const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const navigate = useNavigate();
  const [user, setUser] = useState<Me | null>(null);
  const [loading, setLoading] = useState(true);
  const [unavailable, setUnavailable] = useState(false);
  useEffect(() => {
    configureApiHandlers({
      onUnauthorized: () => {
        clearCsrfToken();
        setUser(null);
        setUnavailable(false);
        navigate("/login", { replace: true });
      },
    });
    return () => configureApiHandlers({});
  }, [navigate]);

  const probeGeneration = useRef(0);

  const probeSession = useCallback(async () => {
    const current = ++probeGeneration.current;
    setLoading(true);
    const result = await fetchMe();
    if (current !== probeGeneration.current) return;
    if (result.status === "authenticated") {
      setUser(result.me);
      setUnavailable(false);
    } else {
      setUser(null);
      setUnavailable(result.status === "unavailable");
    }
    setLoading(false);
  }, []);

  useEffect(() => {
    void probeSession();
    return () => {
      probeGeneration.current += 1;
    };
  }, [probeSession]);

  async function logout(): Promise<void> {
    try {
      await postLogout();
    } catch {
      // Aunque falle la revocación remota, el estado local se limpia igual.
    } finally {
      setUser(null);
      clearCsrfToken();
    }
  }

  return (
    <AuthContext.Provider value={{ user, loading, unavailable, retrySession: probeSession, logout }}>
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext);
  if (ctx === null) {
    throw new Error("useAuth debe usarse dentro de <AuthProvider>");
  }
  return ctx;
}

/**
 * Guard de rutas protegidas: mientras comprueba la sesión muestra un cursor;
 * sin usuario autenticado redirige a `/login`. El modo invitado no cuenta.
 * Las rutas hijas se renderizan con `<Outlet>`.
 */
export function RequireAuth() {
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
      <div className="flex min-h-screen flex-col items-center justify-center gap-4">
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
    return <Navigate to="/login" replace />;
  }

  return <Outlet />;
}
