"use client";

import { useEffect, useState } from "react";

import { get } from "./api";

export type QueryKey = [string, Record<string, string | number | boolean | undefined>, number];

/** Fetch on key change; loading is derived (no synchronous setState inside effects). */
export function useApi<T>(key: QueryKey | null): { data: T | null; loading: boolean; error: string | null } {
  const k = key ? JSON.stringify(key) : null;
  const [state, setState] = useState<{ key: string | null; data: T | null; error: string | null }>({ key: null, data: null, error: null });
  useEffect(() => {
    if (!k) return;
    let live = true;
    const [path, params] = JSON.parse(k) as QueryKey;
    get<T>(path, params)
      .then((data) => live && setState({ key: k, data, error: null }))
      .catch((e: Error) => live && setState({ key: k, data: null, error: e.message }));
    return () => {
      live = false;
    };
  }, [k]);
  if (!k) return { data: null, loading: false, error: null };
  return { data: state.data, loading: state.key !== k, error: state.key === k ? state.error : null };
}
