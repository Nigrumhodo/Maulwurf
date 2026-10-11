import { Card } from "@/components/ui/Card";

// D1.2 — Materias esqueleto. El CRUD contra la API real llega en D1.6.
export function MateriasPage() {
  return (
    <div className="flex flex-col gap-6">
      <h1 className="lowercase text-2xl text-mint">materias</h1>

      <Card title="tus materias">
        <p className="lowercase text-lavender">el alta de materias llega en d1.6.</p>
      </Card>
    </div>
  );
}
