"use client";

// Static export has no server redirect, so the entry route resolves on the client:
// signed-in visitors go to their own console, everyone else to the shared login page.

import { useEffect } from "react";
import { useAuth } from "@/lib/auth";

export default function IndexPage() {
  const { user, loading } = useAuth();

  useEffect(() => {
    if (loading) return;
    window.location.replace(user ? user.home_route : "/login/");
  }, [user, loading]);

  return (
    <main className="flex min-h-screen items-center justify-center">
      <p className="text-sm text-ink-faint">Routing to your console…</p>
    </main>
  );
}
