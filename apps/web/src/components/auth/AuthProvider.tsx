import { createContext, useContext, useEffect, useState } from "react";
import type { ReactNode } from "react";
import { Navigate, Outlet } from "react-router-dom";
import { fetchMe, postLogout } from "@/lib/auth";
import type { Me } from "@/lib/auth";
import { clearCsrfToken, getCsrfToken } from "@/lib/csrf";
import { BlinkingCaret } from "@/components/motion/BlinkingCaret";

// D1.2/D1.4 — Estado de sesión compartido. `GET /me` decide si hay sesión (cookie
// `mw_session`); sin sesión el backend responde 401 y aquí se queda `user=null`.
// El token CSRF se mantiene solo en memoria (`lib/csrf.ts`), nunca en storage
// (regla D1.4/D2.8). El modo invitado es provisional (maqueta): solo desbloquea
// la navegación, sin permisos reales del backend.

const GUEST_KEY = "maulwurf-guest";

interface AuthContextValue {
  user: Me | null;
  csrfToken: string | null;
  loading: boolean;
  guest: boolean;
  enterAsGuest: () => void;
  logout: () => Promise<void>;
}

const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<Me | null>(null);
  const [loading, setLoading] = useState(true);
  const [guest, setGuest] = useState(() => {
    if (typeof window === "undefined") return false;
    try {
      return window.sessionStorage.getItem(GUEST_KEY) === "1";
    } catch {
      return false;
    }
  });

  useEffect(() => {
    let cancelled = false;
    fetchMe().then((me) => {
      if (cancelled) return;
      if (me !== null) {
        setUser(me);
      }
      setLoading(false);
    });
    return () => {
      cancelled = true;
    };
  }, []);

  function enterAsGuest(): void {
    setGuest(true);
    try {
      window.sessionStorage.setItem(GUEST_KEY, "1");
    } catch {
      // almacenamiento no disponible: el invitado vive solo en memoria.
    }
  }

  async function logout(): Promise<void> {
    try {
      await postLogout();
    } catch {
      // Aunque falle la revocación remota, el estado local se limpia igual.
    } finally {
      setUser(null);
      clearCsrfToken();
      setGuest(false);
      try {
        window.sessionStorage.removeItem(GUEST_KEY);
      } catch {
        // ignorar
      }
    }
  }

  return (
    <AuthContext.Provider
      value={{ user, csrfToken: getCsrfToken(), loading, guest, enterAsGuest, logout }}
    >
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
 * sin sesión (y sin modo invitado) redirige a `/login`. Las rutas hijas se
 * renderizan con `<Outlet>`.
 */
export function RequireAuth() {
  const { user, guest, loading } = useAuth();

  if (loading) {
    return (
      <div className="flex min-h-screen items-center justify-center">
        <p className="lowercase text-lavender">
          cargando <BlinkingCaret />
        </p>
      </div>
    );
  }

  if (user === null && !guest) {
    return <Navigate to="/login" replace />;
  }

  return <Outlet />;
}
