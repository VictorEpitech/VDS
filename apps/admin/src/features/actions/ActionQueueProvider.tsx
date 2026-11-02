import { useQueryClient } from "@tanstack/react-query";
import { createContext, useContext, useEffect, useMemo, useSyncExternalStore, type ReactNode } from "react";
import { useApi } from "../../lib/api";
import { keys } from "../../lib/queries";
import { ActionTray } from "./ActionTray";
import { ActionQueue, type Batch } from "./queue";

const Ctx = createContext<ActionQueue | null>(null);

/** File d'actions au niveau de l'application : survit à la navigation, avertit avant la fermeture de l'onglet. */
export function ActionQueueProvider({ children }: { children: ReactNode }) {
  const api = useApi();
  const qc = useQueryClient();
  const queue = useMemo(
    () =>
      new ActionQueue(api, () => {
        void qc.invalidateQueries({ queryKey: keys.screens });
        void qc.invalidateQueries({ queryKey: ["power", "screen"] });
        void qc.invalidateQueries({ queryKey: ["fleet", "apk"] });
      }),
    [api, qc],
  );
  useEffect(() => {
    const warn = (e: BeforeUnloadEvent) => {
      if (!queue.hasUnfinished()) return;
      e.preventDefault();
      e.returnValue = "";
    };
    window.addEventListener("beforeunload", warn);
    return () => {
      window.removeEventListener("beforeunload", warn);
      queue.dispose();
    };
  }, [queue]);
  return (
    <Ctx.Provider value={queue}>
      <ActionTray />
      {children}
    </Ctx.Provider>
  );
}

export function useActionQueue(): { queue: ActionQueue; batches: readonly Batch[] } {
  const queue = useContext(Ctx);
  if (!queue) throw new Error("useActionQueue : ActionQueueProvider manquant");
  const batches = useSyncExternalStore(queue.subscribe, queue.getSnapshot);
  return { queue, batches };
}
