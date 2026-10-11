import { Card } from "@/components/ui/Card";

// D1.2 — Biblioteca esqueleto. El listado real de clases llega en D2.4.
export function BibliotecaPage() {
  return (
    <div className="flex flex-col gap-6">
      <h1 className="lowercase text-2xl text-mint">biblioteca</h1>

      <Card title="tus clases">
        <p className="lowercase text-lavender">aún no has subido ninguna clase.</p>
        <p className="lowercase text-lavender">el upload llega en d2.1.</p>
      </Card>
    </div>
  );
}
