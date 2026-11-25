import { useTranslation } from "react-i18next";
import { UploadForm } from "@/components/upload/UploadForm";
import { useDocumentTitle } from "@/lib/useDocumentTitle";

// D2.1/D2.2 — Subida de clase (M2): el formulario reserva la clase con `POST /audios`
// (consentimiento versionado, idioma sin preselección, límites publicados y «audio no
// conservado») y envía el binario en streaming a la URL relativa de la reserva.
export function SubirClasePage() {
  const { t } = useTranslation("study");
  useDocumentTitle(t("subirPage.docTitle"));
  return (
    <section className="lowercase font-pixel min-w-0 max-w-full border-2 border-pure-black bg-surface shadow-block">
      <header className="flex flex-col gap-1 border-b-2 border-lavender/30 p-4 sm:p-6">
        <h1 className="capitalize text-2xl text-mint">{t("subirPage.title")}</h1>
        <p className="text-base text-lavender/80 max-w-prose">
          {t("subirPage.lead")}
        </p>
      </header>
      <div className="p-4 sm:p-6">
        <UploadForm />
      </div>
    </section>
  );
}
