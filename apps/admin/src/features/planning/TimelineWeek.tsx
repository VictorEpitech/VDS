import { useMemo } from "react";
import { addDays } from "./time";
import { TimelineBase, type TimelineProps } from "./TimelineDay";

/** Vue Semaine : sept journées empilées (premier jour selon la langue), mêmes pistes que la vue Jour. */
export function TimelineWeek({ start, ...p }: TimelineProps & { start: string }) {
  const dates = useMemo(() => Array.from({ length: 7 }, (_, i) => addDays(start, i)), [start]);
  return <TimelineBase dates={dates} {...p} />;
}
