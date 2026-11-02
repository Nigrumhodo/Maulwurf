import { Card } from "@/components/ui/Card";
import { UploadForm } from "@/components/upload/UploadForm";

// D2.1/D2.2 — Subida de clase (M2): el formulario reserva la clase con `POST /audios`
// (consentimiento versionado, idioma sin preselección, límites publicados y «audio no
// conservado») y envía el binario en streaming a la URL relativa de la reserva.
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
