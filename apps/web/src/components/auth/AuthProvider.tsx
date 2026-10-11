import { createContext, useContext, useEffect, useState } from "react";
import type { ReactNode } from "react";
import { Navigate, Outlet } from "react-router-dom";
import { fetchMe, postLogout } from "@/lib/auth";
import type { Me } from "@/lib/auth";
import { BlinkingCaret } from "@/components/motion/BlinkingCaret";

// D1.2 — Estado de sesión compartido. `GET /me` decide si hay sesión (cookie
// `mw_session`); sin sesión el backend responde 401 y aquí se queda `user=null`.
// El token CSRF se mantiene solo en memoria (estado React), nunca en storage
// (regla D1.4/D2.8).

interface AuthContextValue {
  user: Me | null;
  csrfToken: string | null;
  loading: boolean;
  logout: () => Promise<void>;
}

const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<Me | null>(null);
  const [csrfToken, setCsrfToken] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    fetchMe().then((me) => {
      if (cancelled) return;
      if (me !== null) {
        setUser(me);
        setCsrfToken(me.csrf_token);
      }
      setLoading(false);
    });
    return () => {
      cancelled = true;
    };
  }, []);

  async function logout(): Promise<void> {
    try {
      await postLogout(csrfToken);
    } catch {
      // Aunque falle la revocación remota, el estado local se limpia igual.
    } finally {
      setUser(null);
      setCsrfToken(null);
    }
  }

  return (
    <AuthContext.Provider value={{ user, csrfToken, loading, logout }}>
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
 * sin sesión redirige a `/login`. Las rutas hijas se renderizan con `<Outlet>`.
 */
export function RequireAuth() {
  const { user, loading } = useAuth();

  if (loading) {
    return (
      <div className="flex min-h-screen items-center justify-center">
        <p className="lowercase text-lavender">
          cargando <BlinkingCaret />
        </p>
      </div>
    );
  }

  if (user === null) {
    return <Navigate to="/login" replace />;
  }

  return <Outlet />;
}
