import { Card } from "@/components/ui/Card";
import { UploadForm } from "@/components/upload/UploadForm";

// D1.7 — Borrador estático de la subida de clase (sin wiring): formulario de
// upload (consentimiento versionado, idioma sin preselección, «audio no
// conservado»). El envío real de bytes llega en D2.1/D2.2.
export function SubirClasePage() {
  return (
    <div className="flex flex-col gap-6">
      <h1 className="lowercase text-2xl text-mint">subir clase</h1>

      <Card title="datos de la clase" variant="accent">
        <UploadForm />
      </Card>
    </div>
  );
}
