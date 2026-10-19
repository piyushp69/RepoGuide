import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import App from "./App";
import { CodeViewerProvider } from "./components/CodeViewer";
import { AppStateProvider } from "./state/AppState";
import "./index.css";

const queryClient = new QueryClient({
  defaultOptions: {
    queries: { staleTime: 5_000, retry: 1, refetchOnWindowFocus: false },
  },
});

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <AppStateProvider>
        <BrowserRouter>
          <CodeViewerProvider>
            <App />
          </CodeViewerProvider>
        </BrowserRouter>
      </AppStateProvider>
    </QueryClientProvider>
  </StrictMode>,
);
