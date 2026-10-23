/** Témoin de vie de la boucle principale (lu par le watchdog, écrit par le moteur et l'appairage). */
let last = performance.now();

export function mark(): void {
  last = performance.now();
}

export function age(): number {
  return performance.now() - last;
}
