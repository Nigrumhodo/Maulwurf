import i18n from "@/i18n";
import { useCallback, useEffect, useRef, useState } from "react";

export interface ServerList<T> {
  items: T[];
  loading: boolean;
  error: string | null;
}

/**
 * Load a list once, and ignore the response if the component unmounts
 * or a newer load has started.
 */
export function useServerList<T>(load: () => Promise<T[]>): ServerList<T> {
  const [items, setItems] = useState<T[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const generation = useRef(0);

  const run = useCallback(async () => {
    const current = ++generation.current;
    setLoading(true);
    setError(null);
    try {
      const next = await load();
      if (current !== generation.current) return;
      setItems(next);
    } catch (caught) {
      if (current !== generation.current) return;
      setItems([]);
      setError(caught instanceof Error ? caught.message : i18n.t("common:errors.listLoad"));
    } finally {
      if (current === generation.current) setLoading(false);
    }
  }, [load]);

  useEffect(() => {
    void run();
    return () => {
      generation.current += 1;
    };
  }, [run]);

  return { items, loading, error };
}
