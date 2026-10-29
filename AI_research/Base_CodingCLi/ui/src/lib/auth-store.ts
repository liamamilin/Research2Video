import { create } from "zustand";
import type { User } from "@/api/types";
import * as authApi from "@/api";
import { ApiError } from "@/api/client";

interface AuthState {
  user: User | null;
  loading: boolean;
  error: string;
  setUser: (user: User | null) => void;
  login: (username: string, password: string, remember?: boolean) => Promise<void>;
  logout: () => Promise<void>;
  checkAuth: () => Promise<void>;
}

export const useAuthStore = create<AuthState>((set) => ({
  user: null,
  loading: true,
  error: "",

  setUser: (user) => set({ user, loading: false, error: "" }),

  login: async (username, password, remember = false) => {
    const resp = await authApi.login(username, password, remember);
    set({ user: resp.user, loading: false, error: "" });
  },

  logout: async () => {
    // Clear local state first: a failed request must not strand the user in a
    // half-authenticated shell where every page keeps erroring.
    set({ user: null, loading: false, error: "" });
    await authApi.logout().catch(() => {});
  },

  checkAuth: async () => {
    set({ loading: true, error: "" });
    try {
      const user = await authApi.getMe();
      set({ user, loading: false, error: "" });
    } catch (error) {
      if (error instanceof ApiError && error.status === 401) {
        set({ user: null, loading: false, error: "" });
      } else {
        set({ loading: false, error: "暂时无法恢复会话，请重试。登录信息仍保留在浏览器中。" });
      }
    }
  },
}));
