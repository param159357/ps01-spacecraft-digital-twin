"use client";

// Session context. The server is the authority on role: this only mirrors what
// /api/auth/me returns, so the client guard is convenience, never enforcement.

import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import { ApiError, api } from "./api";
import type { Role, SessionUser } from "./types";

interface AuthState {
  user: SessionUser | null;
  loading: boolean;
  error: string | null;
  refresh: () => Promise<void>;
  logout: () => Promise<void>;
}

const AuthContext = createContext<AuthState | null>(null);

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [user, setUser] = useState<SessionUser | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    setLoading(true);
    try {
      const me = await api.get<SessionUser>("/auth/me");
      setUser(me);
      setError(null);
    } catch (err) {
      setUser(null);
      // 401 is the ordinary signed-out case, not an error worth showing.
      if (err instanceof ApiError && err.isUnauthenticated) {
        setError(null);
      } else {
        setError(err instanceof Error ? err.message : "Could not reach the API.");
      }
    } finally {
      setLoading(false);
    }
  }, []);

  const logout = useCallback(async () => {
    try {
      await api.post<void>("/auth/logout");
    } finally {
      setUser(null);
      window.location.assign("/login/");
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const value = useMemo<AuthState>(
    () => ({ user, loading, error, refresh, logout }),
    [user, loading, error, refresh, logout],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthState {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used inside AuthProvider");
  return ctx;
}

/**
 * Client-side route guard: sends a signed-out visitor to the login page, and a
 * signed-in visitor on the wrong console back to their own. The backend enforces the
 * real boundary on every request regardless of what happens here.
 */
export function useRequireRole(role: Role): { user: SessionUser | null; checking: boolean } {
  const { user, loading } = useAuth();

  useEffect(() => {
    if (loading) return;
    if (!user) {
      window.location.assign("/login/");
      return;
    }
    if (user.role !== role) {
      window.location.assign(user.home_route);
    }
  }, [user, loading, role]);

  return { user: user && user.role === role ? user : null, checking: loading };
}
