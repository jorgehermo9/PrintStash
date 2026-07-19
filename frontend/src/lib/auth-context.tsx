"use client";

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useState,
} from "react";
import {
  getUser,
  isLoggedIn,
  storeLogin,
  clearLogin,
  onAuthChange,
  type StoredUser,
} from "@/lib/auth-store";
import { login as apiLogin, logout as apiLogout, getMe } from "@/lib/api";

interface AuthState {
  user: StoredUser | null;
  loading: boolean;
  login: (username: string, password: string, remember_me?: boolean) => Promise<void>
  logout: () => Promise<void>;
  refresh: () => Promise<void>;
}

const AuthContext = createContext<AuthState>({
  user: null,
  loading: true,
  login: async () => {},
  logout: async () => {},
  refresh: async () => {},
});

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [user, setUser] = useState<StoredUser | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let alive = true;
    const off = onAuthChange(() => {
      setUser(getUser());
    });

    if (!isLoggedIn()) {
      setLoading(false);
      return off;
    }

    getMe()
      .then((u) => {
        if (!alive) return;
        const stored: StoredUser = {
          id: u.id,
          username: u.username,
          email: u.email,
          is_superuser: u.is_superuser,
        };
        storeLogin("", stored, { silent: true });
        setUser(stored);
      })
      .catch(() => {
        if (!alive) return;
        clearLogin();
      })
      .finally(() => {
        if (alive) setLoading(false);
      });

    return () => {
      alive = false;
      off();
    };
  }, []);

  const login = useCallback(async (username: string, password: string, remember_me: boolean = false ) => {
    const token = await apiLogin({ username, password, remember_me });
    storeLogin(token.access_token, { id: 0, username, email: null, is_superuser: false });
    try {
      const me = await getMe();
      const stored: StoredUser = {
        id: me.id,
        username: me.username,
        email: me.email,
        is_superuser: me.is_superuser,
      };
      storeLogin(token.access_token, stored, { silent: true });
      setUser(stored);
    } catch (e) {
      clearLogin();
      throw e;
    }
  }, []);

  const logout = useCallback(async () => {
    try {
      await apiLogout();
    } finally {
      clearLogin();
      setUser(null);
    }
  }, []);

  const refresh = useCallback(async () => {
    try {
      const me = await getMe();
      const stored: StoredUser = {
        id: me.id,
        username: me.username,
        email: me.email,
        is_superuser: me.is_superuser,
      };
      storeLogin("", stored, { silent: true });
      setUser(stored);
    } catch (error) {
      clearLogin();
      setUser(null);
      throw error;
    }
  }, []);

  return (
    <AuthContext.Provider value={{ user, loading, login, logout, refresh }}>
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth(): AuthState {
  return useContext(AuthContext);
}
