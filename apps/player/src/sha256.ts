/** SHA-256 incrémental (crypto.subtle n'est ni incrémental ni toujours disponible). */
const K = new Uint32Array([
  0x428a2f98, 0x71374491, 0xb5c0fbcf, 0xe9b5dba5, 0x3956c25b, 0x59f111f1, 0x923f82a4, 0xab1c5ed5,
  0xd807aa98, 0x12835b01, 0x243185be, 0x550c7dc3, 0x72be5d74, 0x80deb1fe, 0x9bdc06a7, 0xc19bf174,
  0xe49b69c1, 0xefbe4786, 0x0fc19dc6, 0x240ca1cc, 0x2de92c6f, 0x4a7484aa, 0x5cb0a9dc, 0x76f988da,
  0x983e5152, 0xa831c66d, 0xb00327c8, 0xbf597fc7, 0xc6e00bf3, 0xd5a79147, 0x06ca6351, 0x14292967,
  0x27b70a85, 0x2e1b2138, 0x4d2c6dfc, 0x53380d13, 0x650a7354, 0x766a0abb, 0x81c2c92e, 0x92722c85,
  0xa2bfe8a1, 0xa81a664b, 0xc24b8b70, 0xc76c51a3, 0xd192e819, 0xd6990624, 0xf40e3585, 0x106aa070,
  0x19a4c116, 0x1e376c08, 0x2748774c, 0x34b0bcb5, 0x391c0cb3, 0x4ed8aa4a, 0x5b9cca4f, 0x682e6ff3,
  0x748f82ee, 0x78a5636f, 0x84c87814, 0x8cc70208, 0x90befffa, 0xa4506ceb, 0xbef9a3f7, 0xc67178f2,
]);

export class Sha256 {
  private h = new Uint32Array([
    0x6a09e667, 0xbb67ae85, 0x3c6ef372, 0xa54ff53a, 0x510e527f, 0x9b05688c, 0x1f83d9ab, 0x5be0cd19,
  ]);
  private buf = new Uint8Array(64);
  private w = new Uint32Array(64);
  private fill = 0;
  private total = 0;

  update(data: Uint8Array): this {
    this.total += data.length;
    let i = 0;
    if (this.fill > 0) {
      const n = Math.min(64 - this.fill, data.length);
      this.buf.set(data.subarray(0, n), this.fill);
      this.fill += n;
      i = n;
      if (this.fill === 64) {
        this.block(this.buf, 0);
        this.fill = 0;
      }
    }
    for (; i + 64 <= data.length; i += 64) this.block(data, i);
    if (i < data.length) {
      this.buf.set(data.subarray(i), 0);
      this.fill = data.length - i;
    }
    return this;
  }

  hex(): string {
    const bits = this.total * 8;
    const pad = new Uint8Array(((this.fill < 56 ? 56 : 120) - this.fill) + 8);
    pad[0] = 0x80;
    const view = new DataView(pad.buffer);
    view.setUint32(pad.length - 8, Math.floor(bits / 0x100000000));
    view.setUint32(pad.length - 4, bits >>> 0);
    const saved = this.total;
    this.update(pad);
    this.total = saved;
    return Array.from(this.h, (x) => x.toString(16).padStart(8, "0")).join("");
  }

  private block(d: Uint8Array, o: number): void {
    const w = this.w;
    for (let t = 0; t < 16; t++) {
      const j = o + t * 4;
      w[t] = ((d[j]! << 24) | (d[j + 1]! << 16) | (d[j + 2]! << 8) | d[j + 3]!) >>> 0;
    }
    for (let t = 16; t < 64; t++) {
      const a = w[t - 15]!;
      const b = w[t - 2]!;
      const s0 = ((a >>> 7) | (a << 25)) ^ ((a >>> 18) | (a << 14)) ^ (a >>> 3);
      const s1 = ((b >>> 17) | (b << 15)) ^ ((b >>> 19) | (b << 13)) ^ (b >>> 10);
      w[t] = (w[t - 16]! + s0 + w[t - 7]! + s1) >>> 0;
    }
    const st = this.h;
    let a: number = st[0]!, b: number = st[1]!, c: number = st[2]!, d2: number = st[3]!;
    let e: number = st[4]!, f: number = st[5]!, g: number = st[6]!, h: number = st[7]!;
    for (let t = 0; t < 64; t++) {
      const S1 = ((e >>> 6) | (e << 26)) ^ ((e >>> 11) | (e << 21)) ^ ((e >>> 25) | (e << 7));
      const ch = (e & f) ^ (~e & g);
      const t1: number = (h + S1 + ch + K[t]! + w[t]!) >>> 0;
      const S0: number = ((a >>> 2) | (a << 30)) ^ ((a >>> 13) | (a << 19)) ^ ((a >>> 22) | (a << 10));
      const maj = (a & b) ^ (a & c) ^ (b & c);
      const t2: number = (S0 + maj) >>> 0;
      h = g; g = f; f = e; e = (d2 + t1) >>> 0; d2 = c; c = b; b = a; a = (t1 + t2) >>> 0;
    }
    const s = this.h;
    s[0] = (s[0]! + a) >>> 0; s[1] = (s[1]! + b) >>> 0; s[2] = (s[2]! + c) >>> 0; s[3] = (s[3]! + d2) >>> 0;
    s[4] = (s[4]! + e) >>> 0; s[5] = (s[5]! + f) >>> 0; s[6] = (s[6]! + g) >>> 0; s[7] = (s[7]! + h) >>> 0;
  }
}

/** Hache un flux en rendant la main au navigateur entre les blocs (pas de gel de la lecture). */
export async function sha256Stream(body: ReadableStream<Uint8Array>): Promise<string> {
  const hash = new Sha256();
  const reader = body.getReader();
  let sinceYield = 0;
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    hash.update(value);
    sinceYield += value.length;
    if (sinceYield >= 4 * 1024 * 1024) {
      sinceYield = 0;
      await new Promise((r) => setTimeout(r, 0));
    }
  }
  return hash.hex();
}
