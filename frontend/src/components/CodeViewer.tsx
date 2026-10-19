import { useQuery } from "@tanstack/react-query";
import { Highlight, themes } from "prism-react-renderer";
import { ArrowDownRight, ArrowUpLeft, FileCode2 } from "lucide-react";
import { createContext, useCallback, useContext, useState, type ReactNode } from "react";
import { api } from "../api/client";
import { useAppState } from "../state/AppState";
import { ErrorBox, Modal, Spinner } from "./ui";

export interface CodeTarget {
  path: string;
  start?: number;
  end?: number;
  chunkId?: string | null;
  title?: string;
}

const PRISM_LANG: Record<string, string> = {
  python: "python", typescript: "tsx", javascript: "jsx", json: "json", yaml: "yaml", markdown: "markdown",
  css: "css", scss: "css", html: "markup", xml: "markup", sql: "sql", go: "go", rust: "rust", c: "c",
  cpp: "cpp", kotlin: "kotlin", swift: "swift", graphql: "graphql",
};

const OpenContext = createContext<(target: CodeTarget) => void>(() => undefined);

/** Opens the shared code viewer, e.g. when a citation is clicked. */
export function useOpenCode() {
  return useContext(OpenContext);
}

export function CodeViewerProvider({ children }: { children: ReactNode }) {
  const [target, setTarget] = useState<CodeTarget | null>(null);
  const open = useCallback((next: CodeTarget) => setTarget(next), []);
  return (
    <OpenContext.Provider value={open}>
      {children}
      {target && <CodeViewerModal target={target} onClose={() => setTarget(null)} onNavigate={setTarget} />}
    </OpenContext.Provider>
  );
}

function CodeViewerModal({ target, onClose, onNavigate }: {
  target: CodeTarget;
  onClose: () => void;
  onNavigate: (target: CodeTarget) => void;
}) {
  const { projectId, isDark } = useAppState();
  const context = target.start ? 8 : 0;
  const slice = useQuery({
    queryKey: ["file", projectId, target.path, target.start, target.end],
    queryFn: () => api.file(projectId as number, target.path, target.start, target.end, context),
    enabled: projectId !== null,
  });
  const related = useQuery({
    queryKey: ["related", projectId, target.chunkId],
    queryFn: () => api.related(projectId as number, target.chunkId as string),
    enabled: projectId !== null && !!target.chunkId,
  });
  const range = target.start ? `:${target.start}${target.end && target.end !== target.start ? `-${target.end}` : ""}` : "";

  return (
    <Modal open onClose={onClose} wide title={<span className="font-mono">{target.path}{range}</span>}>
      {slice.isLoading && <Spinner label="Loading file…" />}
      {slice.error && <ErrorBox error={slice.error} />}
      {slice.data && (
        <div className="space-y-3">
          {target.title && <div className="text-sm text-ink-2">{target.title}</div>}
          <div className="overflow-x-auto rounded-lg border border-line bg-sunken text-[13px] leading-5">
            <Highlight
              code={slice.data.lines.join("\n")}
              language={PRISM_LANG[slice.data.language] ?? "plain"}
              theme={isDark ? themes.vsDark : themes.github}
            >
              {({ tokens, getLineProps, getTokenProps }) => (
                <pre className="min-w-full py-2 font-mono" style={{ background: "transparent" }}>
                  {tokens.map((line, index) => {
                    const number = slice.data.view_start + index;
                    const cited = !!target.start && number >= slice.data.start_line && number <= slice.data.end_line;
                    const props = getLineProps({ line });
                    return (
                      <div
                        key={number}
                        {...props}
                        className={`flex ${cited ? "bg-accent-soft" : ""}`}
                        style={{ ...props.style, background: undefined }}
                      >
                        <span
                          className={`w-12 shrink-0 select-none border-r pr-3 text-right tabular-nums ${cited ? "border-accent text-accent-ink" : "border-line text-muted"}`}
                          aria-hidden
                        >
                          {number}
                        </span>
                        <span className="whitespace-pre pl-3 pr-4">
                          {line.map((token, key) => (
                            <span key={key} {...getTokenProps({ token })} />
                          ))}
                        </span>
                      </div>
                    );
                  })}
                </pre>
              )}
            </Highlight>
          </div>
          <div className="flex flex-wrap items-center gap-3 text-xs text-muted">
            <span>
              Showing lines {slice.data.view_start}-{slice.data.view_end} of {slice.data.total_lines}
            </span>
            {target.start && (
              <button
                className="text-accent-ink underline"
                onClick={() => onNavigate({ path: target.path, chunkId: target.chunkId })}
              >
                Open whole file
              </button>
            )}
          </div>
          {related.data && (related.data.callers.length > 0 || related.data.callees.length > 0) && (
            <div className="grid gap-3 sm:grid-cols-2">
              <RelatedList
                title="Called by"
                icon={<ArrowUpLeft className="size-3.5" />}
                items={related.data.callers}
                onOpen={onNavigate}
              />
              <RelatedList
                title="Calls"
                icon={<ArrowDownRight className="size-3.5" />}
                items={related.data.callees}
                onOpen={onNavigate}
              />
            </div>
          )}
        </div>
      )}
    </Modal>
  );
}

function RelatedList({ title, icon, items, onOpen }: {
  title: string;
  icon: ReactNode;
  items: { file_path: string; symbol: string | null; start_line: number; end_line: number }[];
  onOpen: (target: CodeTarget) => void;
}) {
  return (
    <div className="rounded-lg border border-line p-3">
      <div className="mb-2 flex items-center gap-1.5 text-xs font-semibold text-ink-2">
        {icon}
        {title}
      </div>
      {items.length === 0 ? (
        <div className="text-xs text-muted">None found by static analysis.</div>
      ) : (
        <ul className="space-y-1">
          {items.map((item) => (
            <li key={`${item.file_path}:${item.start_line}`}>
              <button
                className="flex w-full items-center gap-1.5 rounded px-1 py-0.5 text-left text-xs hover:bg-surface-2"
                onClick={() => onOpen({ path: item.file_path, start: item.start_line, end: item.end_line })}
              >
                <FileCode2 className="size-3.5 shrink-0 text-muted" aria-hidden />
                <span className="truncate font-mono">
                  {item.symbol ?? "module code"} <span className="text-muted">· {item.file_path}:{item.start_line}</span>
                </span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
