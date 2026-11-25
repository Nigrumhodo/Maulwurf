import { useTranslation } from "react-i18next";
import { Link } from "react-router-dom";
import { AuthShell } from "@/components/auth/AuthShell";
import { GoogleButton } from "@/components/auth/GoogleButton";
import { Typewriter } from "@/components/motion/Typewriter";
import { useDocumentTitle } from "@/lib/useDocumentTitle";

const steps = [
  { n: "01", key: "connect" },
  { n: "02", key: "upload" },
  { n: "03", key: "study" },
] as const;

export function RegisterPage() {
  const { t } = useTranslation("auth");
  useDocumentTitle(t("register.documentTitle"));
  return (
    <AuthShell title={t("register.windowTitle")}>
      <div className="lowercase">
        <h1 className="text-2xl text-mint">
          <Typewriter text={t("register.heading")} speed={45} />
        </h1>
        <p className="mt-2 text-lavender">&gt; {t("register.prompt")}</p>
      </div>

      <ol className="lowercase flex flex-col gap-2 text-lavender">
        {steps.map((s) => (
          <li key={s.n} className="flex items-baseline gap-3">
            <span className="text-mint">{s.n}</span>
            {t(`register.steps.${s.key}`)}
          </li>
        ))}
      </ol>

      <GoogleButton label={t("register.google")} />

      <div className="lowercase border-t-2 border-lavender/30 pt-4 text-lavender">
        <p className="mb-1">{t("register.hasAccount")}</p>
        <Link to="/login" className="text-mint hover:text-lavender">
          › {t("register.signIn")}
        </Link>
      </div>
    </AuthShell>
  );
}
