// Only an event marker is shared between tabs. Credentials stay in HttpOnly cookies.
export const SESSION_EVENT_KEY = "arec.session-event";
let localRevision = 0;

export function sessionRevision(): string {
  try {
    return `${localRevision}:${localStorage.getItem(SESSION_EVENT_KEY) || ""}`;
  } catch {
    return String(localRevision);
  }
}

export function announceSessionChange(action: "login" | "logout") {
  localRevision++;
  try {
    // This is an event identifier, not an authentication nonce. It also works
    // at the console's plain HTTP LAN address, where randomUUID is unavailable.
    localStorage.setItem(SESSION_EVENT_KEY, JSON.stringify({ action, id: `${Date.now()}-${Math.random()}` }));
  } catch {
    // Storage-disabled browsers still recover on focus and via the local revision.
  }
}
