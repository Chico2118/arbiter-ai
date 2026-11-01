import { useCallback, useEffect, useRef, useState } from "react";

export interface AsyncState<T> {
  data: T | null;
  error: string | null;
  loading: boolean;
  reload: () => Promise<void>;
  setData: (d: T | null) => void;
}

/** Load data on mount (and when deps change); optionally poll while `poll(data)` is true. */
export function useAsync<T>(
  fn: () => Promise<T>,
  deps: unknown[] = [],
  poll?: { intervalMs: number; while: (data: T | null) => boolean },
): AsyncState<T> {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const fnRef = useRef(fn);
  fnRef.current = fn;
  const alive = useRef(true);

  const reload = useCallback(async () => {
    try {
      const value = await fnRef.current();
      if (!alive.current) return;
      setData(value);
      setError(null);
    } catch (e) {
      if (alive.current) setError(e instanceof Error ? e.message : String(e));
    } finally {
      if (alive.current) setLoading(false);
    }
  }, []);

  useEffect(() => {
    alive.current = true;
    setLoading(true);
    void reload();
    return () => {
      alive.current = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);

  const pollRef = useRef(poll);
  pollRef.current = poll;
  useEffect(() => {
    if (!pollRef.current) return;
    const p = pollRef.current;
    if (!p.while(data)) return;
    const t = window.setTimeout(() => void reload(), p.intervalMs);
    return () => window.clearTimeout(t);
  }, [data, reload]);

  return { data, error, loading, reload, setData };
}
