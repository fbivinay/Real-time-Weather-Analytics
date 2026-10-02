// File name of a captured API response (shared by the app and the capture script).
export function demoKey(path) {
  return decodeURIComponent(path).replace(/^\/api\//, "").replace(/[^a-z0-9]+/gi, "_").replace(/^_+|_+$/g, "") || "root";
}
