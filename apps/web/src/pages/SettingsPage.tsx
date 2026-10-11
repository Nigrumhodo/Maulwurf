import { Card } from "@/components/ui/Card";
import { useAuth } from "@/components/auth/AuthProvider";

// D1.2 — Settings esqueleto. Perfil/zona horaria (PATCH /me) y preferencias
// llegan en D1.x/D3.x/D4.x.
export function SettingsPage() {
  const { user } = useAuth();

  return (
    <div className="flex flex-col gap-6">
      <h1 className="lowercase text-2xl text-mint">settings</h1>

      <Card title="perfil">
        {user === null ? (
          <p className="lowercase text-lavender">sin perfil cargado.</p>
        ) : (
          <dl className="lowercase flex flex-col gap-2 text-lavender">
            <div>
              <dt className="text-mint">&gt; email</dt>
              <dd>{user.email}</dd>
            </div>
            <div>
              <dt className="text-mint">&gt; zona horaria</dt>
              <dd>{user.timezone}</dd>
            </div>
          </dl>
        )}
      </Card>
    </div>
  );
}
