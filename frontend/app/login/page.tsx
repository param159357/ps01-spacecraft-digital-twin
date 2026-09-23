"use client";

// Shared login page for all five consoles.
//
// There is no role selector and no public signup: the server derives the role from the
// authenticated account and tells the browser where to go. Accounts are provisioned with
// scripts/seed_users.py.

import { useEffect, useState } from "react";
import { ApiError, api } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import type { SessionUser } from "@/lib/types";

interface LoginResponse {
  user: SessionUser;
  csrf_token: string;
}

export default function LoginPage() {
  const { user, loading } = useAuth();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  // An already-signed-in visitor should not sit on the login form.
  useEffect(() => {
    if (!loading && user) window.location.replace(user.home_route);
  }, [user, loading]);

  async function handleSubmit(event: React.FormEvent) {
    event.preventDefault();
    setSubmitting(true);
    setError(null);
    try {
      const result = await api.post<LoginResponse>("/auth/login", { username, password });
      // The destination comes from the server, never from a client-side role guess.
      window.location.assign(result.user.home_route);
    } catch (err) {
      setError(
        err instanceof ApiError ? err.message : "Could not reach the API. Is the backend running?",
      );
      setSubmitting(false);
    }
  }

  return (
    <main className="flex min-h-screen flex-col items-center justify-center px-4 py-10">
      <div className="w-full max-w-sm">
        <div className="mb-6 text-center">
          <p className="text-xs font-bold tracking-[0.35em] text-accent">PS01</p>
          <h1 className="mt-2 text-xl font-semibold text-ink">Spacecraft Digital Twin</h1>
          <p className="mt-1 text-xs text-ink-faint">
            Sign in to reach your console. Your role determines which console opens.
          </p>
        </div>

        <form
          onSubmit={handleSubmit}
          className="space-y-4 rounded-md border border-edge bg-panel p-5"
        >
          <div className="space-y-1.5">
            <label htmlFor="username" className="block text-xs tracking-wide text-ink-dim uppercase">
              Username
            </label>
            <input
              id="username"
              name="username"
              autoComplete="username"
              required
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              className="w-full rounded border border-edge-strong bg-void px-3 py-2 text-sm text-ink placeholder:text-ink-faint focus:border-accent focus:outline-none"
              placeholder="e.g. master"
            />
          </div>

          <div className="space-y-1.5">
            <label htmlFor="password" className="block text-xs tracking-wide text-ink-dim uppercase">
              Password
            </label>
            <input
              id="password"
              name="password"
              type="password"
              autoComplete="current-password"
              required
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              className="w-full rounded border border-edge-strong bg-void px-3 py-2 text-sm text-ink focus:border-accent focus:outline-none"
            />
          </div>

          {error ? (
            <p
              role="alert"
              className="rounded border border-critical/40 bg-critical/10 px-3 py-2 text-xs text-critical"
            >
              {error}
            </p>
          ) : null}

          <button
            type="submit"
            disabled={submitting}
            className="w-full rounded bg-accent px-3 py-2 text-sm font-semibold text-void transition-opacity hover:opacity-90 disabled:opacity-50"
          >
            {submitting ? "Signing in…" : "Sign in"}
          </button>
        </form>

        <p className="mt-4 text-center text-[11px] text-ink-faint">
          Fictional demonstration vehicle. Mission-control inspired interface; not an official
          NASA or ISRO system.
        </p>
      </div>
    </main>
  );
}
