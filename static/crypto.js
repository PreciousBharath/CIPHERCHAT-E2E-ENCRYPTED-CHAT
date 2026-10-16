/* Browser-side end-to-end encryption (Web Crypto API). The passphrase and derived key never leave this page. */
window.E2E = {
  b64: buf => btoa(String.fromCharCode(...new Uint8Array(buf))),
  unb64: s => Uint8Array.from(atob(s), c => c.charCodeAt(0)),
  async deriveKey(passphrase, saltB64) {
    const base = await crypto.subtle.importKey("raw", new TextEncoder().encode(passphrase), "PBKDF2", false, ["deriveKey"]);
    return crypto.subtle.deriveKey({ name: "PBKDF2", salt: this.unb64(saltB64), iterations: 150000, hash: "SHA-256" },
      base, { name: "AES-GCM", length: 256 }, false, ["encrypt", "decrypt"]);
  },
  async encrypt(key, text) {
    const iv = crypto.getRandomValues(new Uint8Array(12));
    const ct = await crypto.subtle.encrypt({ name: "AES-GCM", iv }, key, new TextEncoder().encode(text));
    return { iv: this.b64(iv), ct: this.b64(ct) };
  },
  async decrypt(key, ivB64, ctB64) {
    const pt = await crypto.subtle.decrypt({ name: "AES-GCM", iv: this.unb64(ivB64) }, key, this.unb64(ctB64));
    return new TextDecoder().decode(pt);
  },
  newSalt() { return this.b64(crypto.getRandomValues(new Uint8Array(16))); },
};
