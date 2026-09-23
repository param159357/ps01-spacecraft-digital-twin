"use client";

import { useEffect } from "react";
import { AppShell, headerFromLive } from "@/components/AppShell";
import { ConsoleBody } from "@/components/ConsoleBody";
import { useAuth } from "@/lib/auth";
import { useLiveStream } from "@/lib/live";

// Shared history view, scoped by the signed-in account rather than fixed to one role.
export default function HistoryPage() {
  const { user, loading } = useAuth();
  const live = useLiveStream();

  useEffect(() => {
    if (!loading && !user) window.location.assign("/login/");
  }, [user, loading]);

  if (loading || !user) return null;

  return (
    <AppShell role={user.role} runState={headerFromLive(live)}>
      <ConsoleBody
        headline="History and investigation"
        summary="Stored evidence within the permitted scope of this account. Synthetic demonstration data and live runs are stored separately and are never silently mixed; every summary states its dataset, date window and coverage."
        pending={[
          {
            title: "Period summaries",
            phase: 6,
            what: "Daily, weekly and monthly incident counts, durations and coverage over a selected source and window.",
          },
          {
            title: "Recurrence evidence",
            phase: 6,
            what: "Distinct recurring incidents separated from one unresolved incident sampled many times, with command-versus-outcome comparison.",
          },
          {
            title: "Period review",
            phase: 7,
            what: "Generated interpretation of the computed facts, cached per window and labelled with how it was produced.",
          },
        ]}
      />
    </AppShell>
  );
}
