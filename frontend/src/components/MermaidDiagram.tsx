import { useEffect, useId, useState } from "react";
import { useAppState } from "../state/AppState";
import { ErrorBox, Spinner } from "./ui";

/** Renders Mermaid source locally (the library is bundled; nothing is fetched from the network). */
export function MermaidDiagram({ code }: { code: string }) {
  const { isDark } = useAppState();
  const id = useId().replace(/[^a-zA-Z0-9]/g, "");
  const [svg, setSvg] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setSvg(null);
    setError(null);
    (async () => {
      try {
        const mermaid = (await import("mermaid")).default;
        mermaid.initialize({
          startOnLoad: false,
          securityLevel: "strict",
          theme: isDark ? "dark" : "neutral",
          fontFamily: "system-ui, -apple-system, Segoe UI, sans-serif",
          flowchart: { htmlLabels: true, curve: "basis" },
        });
        // The backend emits light-theme node styles; swap them for dark-surface equivalents.
        const themed = isDark
          ? code
              .replace(/classDef entry [^\n]+/, "classDef entry fill:#132a13,stroke:#0ca30c,stroke-width:2px,color:#e3f4e3")
              .replace(/classDef external [^\n]+/, "classDef external fill:#232322,stroke:#6f6e69,color:#c3c2b7,stroke-dasharray:3 3")
          : code;
        const result = await mermaid.render(`mermaid-${id}-${isDark ? "d" : "l"}`, themed);
        if (!cancelled) setSvg(result.svg);
      } catch (err) {
        if (!cancelled) setError(err instanceof Error ? err.message : String(err));
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [code, id, isDark]);

  if (error) {
    return (
      <div className="space-y-2">
        <ErrorBox error={`The diagram could not be rendered: ${error}`} />
        <pre className="overflow-x-auto rounded-lg bg-sunken p-3 text-xs">{code}</pre>
      </div>
    );
  }
  if (!svg) return <Spinner label="Rendering diagram…" />;
  return <div className="mermaid-host overflow-x-auto" dangerouslySetInnerHTML={{ __html: svg }} />;
}
