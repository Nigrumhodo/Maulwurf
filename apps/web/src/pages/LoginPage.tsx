import { useTranslation } from "react-i18next";
import { Link, useSearchParams } from "react-router-dom";
import { AuthShell } from "@/components/auth/AuthShell";
import { GoogleButton } from "@/components/auth/GoogleButton";
import { Typewriter } from "@/components/motion/Typewriter";
import { useDocumentTitle } from "@/lib/useDocumentTitle";

// D1.3 — Mapeo de errores del callback OAuth a mensajes accionables. Las razones
// las define el backend en `routers/auth.py`; aquí solo se traducen para el usuario.
const OAUTH_ERRORS = [
  "access_denied",
  "invalid_state",
  "missing_params",
  "oauth_failed",
  "oauth_not_configured",
] as const;

type OAuthError = (typeof OAUTH_ERRORS)[number];

function isOAuthError(value: string): value is OAuthError {
  return (OAUTH_ERRORS as readonly string[]).includes(value);
}

export function LoginPage() {
  const { t } = useTranslation("auth");
  useDocumentTitle(t("login.documentTitle"));
  const [searchParams] = useSearchParams();
  const error = searchParams.get("error");
  const message = error
    ? isOAuthError(error)
      ? t(`login.errors.${error}`)
      : t("login.errorFallback")
    : null;

  return (
    <AuthShell title={t("login.windowTitle")}>
      <div className="lowercase">
        <h1 className="text-2xl text-mint">
          <Typewriter text={t("login.heading")} speed={45} />
        </h1>
        <p className="mt-2 text-lavender">&gt; {t("login.prompt")}</p>
      </div>

      {message !== null && (
        <p role="alert" className="lowercase border-2 border-lavender bg-surface px-3 py-2 text-lavender">
          &gt; {message}
        </p>
      )}

      <GoogleButton label={t("login.google")} />

      <div className="lowercase border-t-2 border-lavender/30 pt-4 text-lavender">
        <p className="mb-1">{t("login.noAccount")}</p>
        <Link to="/register" className="text-mint hover:text-lavender">
          › {t("login.createAccount")}
        </Link>
      </div>
    </AuthShell>
  );
}
