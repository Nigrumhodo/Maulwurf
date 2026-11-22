import { useEffect } from "react";

const APP_TITLE = "maulwurf";

/** Sets `document.title` to `<name> · maulwurf` and restores the previous title on unmount. */
export function useDocumentTitle(name: string): void {
  useEffect(() => {
    const previous = document.title;
    document.title = `${name} · ${APP_TITLE}`;
    return () => {
      document.title = previous;
    };
  }, [name]);
}
