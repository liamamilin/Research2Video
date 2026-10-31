import { createContext, useContext, useEffect, type ReactNode } from "react";
import { useAuthStore } from "@/lib/auth-store";
import { setAuthErrorHandler } from "@/api/client";
import { SESSION_EVENT_KEY } from "@/lib/session-events";

interface AuthContextValue {
  isAuthenticated: boolean;
}

const AuthContext = createContext<AuthContextValue>({ isAuthenticated: false });

export function AuthProvider({ children }: { children: ReactNode }) {
  const { user, loading, error, checkAuth, setUser } = useAuthStore();

  useEffect(() => {
    checkAuth();
  }, [checkAuth]);

  useEffect(() => {
    setAuthErrorHandler(() => {
      // An expired request only clears the local view. Sending /logout here
      // could revoke a fresh session established by another tab in the meantime.
      setUser(null);
    });
    return () => setAuthErrorHandler(null);
  }, [setUser]);

  useEffect(() => {
    let lastFocusCheck = 0;
    const onFocus = () => {
      if (document.visibilityState === "hidden" || Date.now() - lastFocusCheck < 2_000) return;
      lastFocusCheck = Date.now();
      void checkAuth(true);
    };
    const onStorage = (event: StorageEvent) => {
      if (event.key !== SESSION_EVENT_KEY || !event.newValue) return;
      try {
        // Ignore an older event queued before a subsequent login/logout.
        if (localStorage.getItem(SESSION_EVENT_KEY) !== event.newValue) return;
        const value = JSON.parse(event.newValue);
        if (value.action === "logout") setUser(null);
        else if (value.action === "login") void checkAuth(true);
      } catch {
        // An unavailable storage area is handled by the focus recovery path.
      }
    };
    window.addEventListener("storage", onStorage);
    window.addEventListener("focus", onFocus);
    document.addEventListener("visibilitychange", onFocus);
    return () => {
      window.removeEventListener("storage", onStorage);
      window.removeEventListener("focus", onFocus);
      document.removeEventListener("visibilitychange", onFocus);
    };
  }, [checkAuth, setUser]);

  if (loading) {
    return (
      <div className="flex h-screen items-center justify-center bg-bg">
        <div className="text-text-muted animate-pulse">Loading...</div>
      </div>
    );
  }

  if (error) {
    return <div className="flex h-screen items-center justify-center bg-bg">
      <div className="card p-6 max-w-md space-y-4 text-sm" role="alert">
        <p>{error}</p>
        <button className="btn" onClick={() => void checkAuth()}>重试恢复会话</button>
      </div>
    </div>;
  }

  return (
    <AuthContext.Provider value={{ isAuthenticated: !!user }}>
      {children}
    </AuthContext.Provider>
  );
}

export const useAuth = () => useContext(AuthContext);
