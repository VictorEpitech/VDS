export type ImportWarning = "ratio_mismatch" | "low_resolution";
export type TargetOrientation = "landscape" | "portrait";

/** En dessous de ce plus grand côté (px), le contenu paraît flou sur un écran Full HD. */
const MIN_LONG_SIDE = 1280;

/** Avertissements d'après les dimensions : jamais un refus, seulement une information. */
export function warningsFor(width: number, height: number, target?: TargetOrientation): ImportWarning[] {
  const out: ImportWarning[] = [];
  if (target && width !== height && (width > height ? "landscape" : "portrait") !== target) out.push("ratio_mismatch");
  if (Math.max(width, height) < MIN_LONG_SIDE) out.push("low_resolution");
  return out;
}

function imageSize(file: File): Promise<{ width: number; height: number } | null> {
  if (typeof createImageBitmap !== "function") return Promise.resolve(null);
  return createImageBitmap(file).then(
    (bmp) => {
      const size = { width: bmp.width, height: bmp.height };
      bmp.close();
      return size;
    },
    () => null,
  );
}

function videoSize(file: File): Promise<{ width: number; height: number } | null> {
  if (typeof URL.createObjectURL !== "function") return Promise.resolve(null);
  return new Promise((resolve) => {
    const video = document.createElement("video");
    const url = URL.createObjectURL(file);
    const done = (size: { width: number; height: number } | null) => {
      clearTimeout(timer);
      URL.revokeObjectURL(url);
      video.removeAttribute("src");
      resolve(size);
    };
    const timer = setTimeout(() => done(null), 5000);
    video.preload = "metadata";
    video.muted = true;
    video.onloadedmetadata = () => done(video.videoWidth > 0 ? { width: video.videoWidth, height: video.videoHeight } : null);
    video.onerror = () => done(null);
    video.src = url;
  });
}

/** Lit les dimensions dans le navigateur (image : bitmap, vidéo : métadonnées) ; illisible ou autre type : aucun avertissement. */
export async function inspectFile(file: File, target?: TargetOrientation): Promise<ImportWarning[]> {
  const size = file.type.startsWith("image/") ? await imageSize(file) : file.type.startsWith("video/") ? await videoSize(file) : null;
  return size ? warningsFor(size.width, size.height, target) : [];
}
