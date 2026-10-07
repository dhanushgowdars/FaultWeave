import { useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import type { ConnectionState, StreamEvent } from "../api/types";

const EVENT_NAMES = [
  "snapshot",
  "intelligence.normal",
  "incident.opened",
  "incident.updated",
  "incident.resolved",
  "stream.error",
] as const;

export function useIntelligenceStream() {
  const queryClient = useQueryClient();
  const [connection, setConnection] = useState<ConnectionState>("connecting");
  const [events, setEvents] = useState<StreamEvent[]>([]);

  useEffect(() => {
    let openedOnce = false;
    const source = new EventSource("/api/v1/intelligence/stream");

    source.onopen = () => {
      openedOnce = true;
      setConnection("connected");
    };
    source.onerror = () => {
      setConnection(openedOnce ? "reconnecting" : "offline");
    };

    const onMessage = (eventName: string) => (message: MessageEvent<string>) => {
      let data: Record<string, unknown> = {};
      try {
        data = JSON.parse(message.data) as Record<string, unknown>;
      } catch {
        data = { raw: message.data };
      }
      setEvents((current) => [
        {
          id: message.lastEventId || `${Date.now()}`,
          event: eventName,
          receivedAt: new Date().toISOString(),
          data,
        },
        ...current,
      ].slice(0, 40));
      void queryClient.invalidateQueries({ queryKey: ["incidents"] });
      void queryClient.invalidateQueries({ queryKey: ["current-incident"] });
      void queryClient.invalidateQueries({ queryKey: ["live-inference"] });
      void queryClient.invalidateQueries({ queryKey: ["telemetry"] });
    };

    const listeners = EVENT_NAMES.map((name) => {
      const listener = onMessage(name);
      source.addEventListener(name, listener);
      return [name, listener] as const;
    });

    return () => {
      listeners.forEach(([name, listener]) => source.removeEventListener(name, listener));
      source.close();
    };
  }, [queryClient]);

  return { connection, events };
}
