import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";

export type ThemeChoice = "system" | "light" | "dark";

interface AppState {
  projectId: number | null;
  setProjectId: (id: number | null) => void;
  learner: string | null;
  setLearner: (name: string | null) => void;
  managerView: boolean;
  setManagerView: (on: boolean) => void;
  theme: ThemeChoice;
  setTheme: (theme: ThemeChoice) => void;
  isDark: boolean;
}

const Context = createContext<AppState | null>(null);

function readStorage(key: string): string | null {
  try {
    return window.localStorage.getItem(key);
  } catch {
    return null;
  }
}

function writeStorage(key: string, value: string | null) {
  try {
    if (value === null) window.localStorage.removeItem(key);
    else window.localStorage.setItem(key, value);
  } catch {
    /* storage unavailable (private mode); state still works for this session */
  }
}

function systemPrefersDark() {
  return window.matchMedia?.("(prefers-color-scheme: dark)").matches ?? false;
}

export function AppStateProvider({ children }: { children: ReactNode }) {
  const [projectId, setProjectIdState] = useState<number | null>(() => {
    const raw = readStorage("rg.project");
    return raw ? Number(raw) : null;
  });
  const [learner, setLearnerState] = useState<string | null>(() => readStorage("rg.learner"));
  const [managerView, setManagerViewState] = useState<boolean>(() => readStorage("rg.manager") === "1");
  const [theme, setThemeState] = useState<ThemeChoice>(() => (readStorage("rg.theme") as ThemeChoice) || "system");
  const [systemDark, setSystemDark] = useState(systemPrefersDark);

  useEffect(() => {
    const media = window.matchMedia?.("(prefers-color-scheme: dark)");
    if (!media) return;
    const listener = () => setSystemDark(media.matches);
    media.addEventListener("change", listener);
    return () => media.removeEventListener("change", listener);
  }, []);

  useEffect(() => {
    const root = document.documentElement;
    if (theme === "system") delete root.dataset.theme;
    else root.dataset.theme = theme;
  }, [theme]);

  const setProjectId = useCallback((id: number | null) => {
    setProjectIdState(id);
    writeStorage("rg.project", id === null ? null : String(id));
  }, []);
  const setLearner = useCallback((name: string | null) => {
    setLearnerState(name);
    writeStorage("rg.learner", name);
  }, []);
  const setManagerView = useCallback((on: boolean) => {
    setManagerViewState(on);
    writeStorage("rg.manager", on ? "1" : null);
  }, []);
  const setTheme = useCallback((value: ThemeChoice) => {
    setThemeState(value);
    writeStorage("rg.theme", value === "system" ? null : value);
  }, []);

  const value = useMemo<AppState>(
    () => ({
      projectId,
      setProjectId,
      learner,
      setLearner,
      managerView,
      setManagerView,
      theme,
      setTheme,
      isDark: theme === "dark" || (theme === "system" && systemDark),
    }),
    [projectId, setProjectId, learner, setLearner, managerView, setManagerView, theme, setTheme, systemDark],
  );
  return <Context.Provider value={value}>{children}</Context.Provider>;
}

export function useAppState(): AppState {
  const value = useContext(Context);
  if (!value) throw new Error("useAppState must be used inside AppStateProvider");
  return value;
}
