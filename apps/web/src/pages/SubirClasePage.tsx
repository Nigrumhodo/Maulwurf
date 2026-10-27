import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { DateInput } from "@/components/ui/DateInput";
import { Input } from "@/components/ui/Input";
import { LanguageSelect } from "@/components/ui/LanguageSelect";
import { TimezoneSelect } from "@/components/ui/TimezoneSelect";

// D1.5/D1.7 (borrador estático) — Formulario de subida: materia, fecha de clase,
// zona horaria e idioma (SIN preselección). El envío real de bytes llega en
// D2.1/D2.2; aquí solo se maqueta el flujo con los componentes base de D1.5.
export function SubirClasePage() {
  return (
    <div className="flex flex-col gap-6">
      <h1 className="lowercase text-2xl text-mint">subir clase</h1>

      <Card title="datos de la clase" variant="accent">
        <div className="grid gap-4 sm:grid-cols-2">
          <Input label="materia" placeholder="cálculo i" />
          <DateInput label="fecha de clase" />
          <TimezoneSelect label="zona horaria" />
          <LanguageSelect label="idioma del audio" />
        </div>

        <p className="mt-4 lowercase text-lavender/60">
          &gt; el consentimiento (borrado, tratamiento cloud, sin reproducción) y
          la subida real llegan en d2.1.
        </p>

        <div className="mt-4 flex items-center gap-2">
          <Button variant="secondary" disabled>
            continuar
          </Button>
        </div>
      </Card>
    </div>
  );
}
