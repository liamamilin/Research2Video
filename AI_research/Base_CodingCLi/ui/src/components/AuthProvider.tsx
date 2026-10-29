import { createContext, useContext, useEffect, type ReactNode } from "react";
import { useAuthStore } from "@/lib/auth-store";
import { setAuthErrorHandler } from "@/api/client";

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
