// vds-content-video : contrat packages/sdk-ts/src/content.ts (CONTENT_API_VERSION 1).
const FITS = ["contain", "cover"];
const LOAD_TIMEOUT_MS = 9000;

export class VdsContentVideo extends HTMLElement {
  #props = null;
  #ctx = null;
  #video = null;

  // Un seul décodeur matériel sur les TV : le player ne précharge pas une vidéo pendant qu'une autre joue.
  get exclusive() {
    return true;
  }

  configure(props, ctx) {
    // `media` : clé de l'item.schema.json (résolue en ressource par le serveur) ; `src` : ancien nom toléré.
    const ref = props?.media ?? props?.src;
    const src = typeof ref === "string" ? ref : "";
    this.#props = {
      src,
      audio: props?.audio === true,
      fit: FITS.includes(props?.fit) ? props.fit : "contain"
    };
    this.#ctx = ctx;
    this.style.cssText = "display:block;width:100%;height:100%;visibility:hidden";
  }

  #fail(code) {
    this.dispatchEvent(new CustomEvent("vds-error", { detail: { code } }));
  }

  prepare() {
    if (!this.#props || !this.#props.src || !this.#ctx) return Promise.reject(new Error("video_config"));
    const video = document.createElement("video");
    video.muted = true;
    video.playsInline = true;
    video.preload = "auto";
    video.controls = false;
    video.disablePictureInPicture = true;
    video.style.cssText = `width:100%;height:100%;object-fit:${this.#props.fit};display:block`;
    this.#video = video;
    return new Promise((resolve, reject) => {
      const settle = (fn, arg) => {
        clearTimeout(timer);
        video.removeEventListener("loadeddata", onReady);
        video.removeEventListener("error", onError);
        fn(arg);
      };
      const onReady = () => settle(resolve);
      const onError = () => {
        this.stop();
        settle(reject, new Error("video_load"));
      };
      const timer = setTimeout(() => {
        this.stop();
        settle(reject, new Error("video_timeout"));
      }, LOAD_TIMEOUT_MS);
      video.addEventListener("loadeddata", onReady, { once: true });
      video.addEventListener("error", onError, { once: true });
      video.src = this.#ctx.resolveUrl(this.#props.src);
      this.replaceChildren(video);
      video.load();
    });
  }

  start() {
    const video = this.#video;
    if (!video) return;
    video.addEventListener("ended", () => this.dispatchEvent(new Event("vds-ended")), { once: true });
    video.addEventListener("error", () => this.#fail("video_playback"), { once: true });
    this.style.visibility = "visible";
    video.muted = !this.#props.audio;
    const started = video.play();
    if (started && typeof started.catch === "function") {
      started.catch(() => {
        // Lecture avec son refusée par le moteur : on retente en muet avant de déclarer l'échec.
        video.muted = true;
        const retry = video.play();
        if (retry && typeof retry.catch === "function") retry.catch(() => this.#fail("video_play"));
      });
    }
  }

  stop() {
    this.style.visibility = "hidden";
    const video = this.#video;
    this.#video = null;
    if (video) {
      video.pause();
      video.removeAttribute("src");
      video.load();
    }
    this.replaceChildren();
  }
}

if (!customElements.get("vds-content-video")) {
  customElements.define("vds-content-video", VdsContentVideo);
}
