import { GOOGLE_AUTH_START_URL } from "@/lib/auth";

function GoogleG() {
  return (
    <svg
      viewBox="0 0 48 48"
      className="h-6 w-6"
      role="img"
      aria-hidden="true"
    >
      <path
        fill="#FFC107"
        d="M43.611 20.083H42V20H24v8h11.303c-1.649 4.657-6.08 8-11.303 8-6.627 0-12-5.373-12-12s5.373-12 12-12c3.059 0 5.842 1.154 7.961 3.039l5.657-5.657C34.046 6.053 29.268 4 24 4 12.955 4 4 12.955 4 24s8.955 20 20 20 20-8.955 20-20c0-1.341-.138-2.65-.389-3.917z"
      />
      <path
        fill="#FF3D00"
        d="M6.306 14.691l6.571 4.819C14.655 15.108 18.961 12 24 12c3.059 0 5.842 1.154 7.961 3.039l5.657-5.657C34.046 6.053 29.268 4 24 4 16.318 4 9.656 8.337 6.306 14.691z"
      />
      <path
        fill="#4CAF50"
        d="M24 44c5.166 0 9.86-1.977 13.409-5.192l-6.19-5.238C29.211 35.091 26.715 36 24 36c-5.202 0-9.619-3.317-11.283-7.946l-6.522 5.025C9.505 39.556 16.227 44 24 44z"
      />
      <path
        fill="#1976D2"
        d="M43.611 20.083H42V20H24v8h11.303c-.792 2.237-2.231 4.166-4.087 5.571.001-.001.002-.001.003-.002l6.19 5.238C36.971 39.205 44 34 44 24c0-1.341-.138-2.65-.389-3.917z"
      />
    </svg>
  );
}

export interface GoogleButtonProps {
  /** texto del botón; por defecto "entrar con google" */
  label?: string;
}

/**
 * Botón de entrada con Google, estética retro. Es un enlace real a
 * `/auth/google/start`, no una petición fetch: la navegación la inicia el
 * backend y el navegador nunca maneja el client secret.
 */
export function GoogleButton({ label = "entrar con google" }: GoogleButtonProps) {
  return (
    <a
      href={GOOGLE_AUTH_START_URL}
      className="lowercase font-pixel inline-flex items-center justify-center gap-3 border-2 border-pure-black bg-pure-white px-6 py-2 text-xl text-pure-black shadow-block transition-none hover:bg-lavender hover:text-on-accent active:translate-x-[2px] active:translate-y-[2px] active:shadow-block-sm"
    >
      <GoogleG />
      {label}
    </a>
  );
}
