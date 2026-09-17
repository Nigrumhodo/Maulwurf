export const metadata = {
  title: "Maulwurf",
  description: "De la grabación de clase al estudio accionable.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="es">
      <body>{children}</body>
    </html>
  );
}
