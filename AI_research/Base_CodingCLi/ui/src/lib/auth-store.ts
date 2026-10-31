import { create } from "zustand";
import type { User } from "@/api/types";
import * as authApi from "@/api";
import { ApiError } from "@/api/client";
import { announceSessionChange, sessionRevision } from "./session-events";

let checkSequence = 0;

interface AuthState {
  user: User | null;
  loading: boolean;
  error: string;
  setUser: (user: User | null) => void;
  login: (username: string, password: string, remember?: boolean) => Promise<void>;
  logout: () => Promise<void>;
  checkAuth: (background?: boolean) => Promise<void>;
}

export const useAuthStore = create<AuthState>((set, get) => ({
  user: null,
  loading: true,
  error: "",

  setUser: (user) => {
    checkSequence++;
    set({ user, loading: false, error: "" });
  },

  login: async (username, password, remember = false) => {
    const resp = await authApi.login(username, password, remember);
    checkSequence++;
    announceSessionChange("login");
    set({ user: resp.user, loading: false, error: "" });
  },

  logout: async () => {
    // Clear local state first: a failed request must not strand the user in a
    // half-authenticated shell where every page keeps erroring.
    const sequence = ++checkSequence;
    const revision = sessionRevision();
    set({ user: null, loading: false, error: "" });
    await authApi.logout().then(() => {
      if (sequence === checkSequence && revision === sessionRevision()) announceSessionChange("logout");
    }).catch(() => {});
  },

  checkAuth: async (background = false) => {
    const sequence = ++checkSequence;
    const revision = sessionRevision();
    if (!background) set({ loading: true, error: "" });
    try {
      const user = await authApi.getMe();
      if (sequence !== checkSequence || revision !== sessionRevision()) return;
      set({ user, loading: false, error: "" });
    } catch (error) {
      if (sequence !== checkSequence || revision !== sessionRevision()) return;
      if (error instanceof ApiError && error.status === 401) {
        set({ user: null, loading: false, error: "" });
      } else if (background && get().user) {
        // A focus check must not hide an open editor for a network interruption.
        set({ loading: false });
      } else {
        set({ loading: false, error: "暂时无法恢复会话，请重试。登录信息仍保留在浏览器中。" });
      }
    }
  },
}));
