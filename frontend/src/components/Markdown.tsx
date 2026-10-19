import { Fragment } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import type { Citation, Source } from "../api/types";
import { useOpenCode } from "./CodeViewer";

const CODE_SPANS = /(```[\s\S]*?(?:```|$)|`[^`\n]*`)/g;
const CITATION = /(?<!\w)\[(\d{1,3}(?:\s*(?:,|-|–)\s*\d{1,3})*)\]/g;

function expand(group: string): number[] {
  const numbers: number[] = [];
  for (const part of group.split(/\s*,\s*/)) {
    const range = part.split(/\s*[-–]\s*/).map(Number);
    if (range.length === 2 && range[1] > range[0] && range[1] - range[0] < 20) {
      for (let n = range[0]; n <= range[1]; n++) numbers.push(n);
    } else {
      numbers.push(...range);
    }
  }
  return numbers;
}

/** Turn [1] / [1, 2] / [1-3] markers (outside code) into links the renderer shows as chips. */
export function linkCitations(text: string): string {
  return text
    .split(CODE_SPANS)
    .map((part, index) =>
      index % 2 === 1 ? part : part.replace(CITATION, (_m, group: string) => expand(group).map((n) => `[${n}](#cite-${n})`).join("")),
    )
    .join("");
}

/** Plain text with `backtick` spans shown as inline code (for short model-written strings). */
export function InlineText({ text }: { text: string }) {
  return (
    <>
      {text.split(/(`[^`\n]+`)/g).map((part, index) =>
        part.length > 2 && part.startsWith("`") && part.endsWith("`") ? (
          <code key={index} className="rounded border border-line bg-surface-2 px-1 font-mono text-[0.85em]">
            {part.slice(1, -1)}
          </code>
        ) : (
          <Fragment key={index}>{part}</Fragment>
        ),
      )}
    </>
  );
}

export function Markdown({ text, sources, citations }: { text: string; sources?: Source[]; citations?: Citation[] }) {
  const openCode = useOpenCode();
  const lookup = (n: number) =>
    citations?.find((c) => c.n === n) ?? sources?.find((s) => s.n === n) ?? null;
  return (
    <div className="prose-rg text-sm text-ink">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          a: ({ href, children }) => {
            if (href?.startsWith("#cite-")) {
              const n = Number(href.slice(6));
              const target = lookup(n);
              if (!target) {
                return <span className="text-muted" title="This excerpt number was not in the context">[{n}]</span>;
              }
              return (
                <button
                  type="button"
                  onClick={() =>
                    openCode({
                      path: target.file_path,
                      start: target.start_line,
                      end: target.end_line,
                      chunkId: target.chunk_id,
                      title: target.symbol ? `${target.symbol}` : undefined,
                    })
                  }
                  className="mx-0.5 inline-flex h-5 min-w-5 items-center justify-center rounded border border-accent/40 bg-accent-soft px-1 align-text-top font-mono text-[11px] font-semibold text-accent-ink no-underline hover:border-accent"
                  title={`${target.file_path}:${target.start_line}-${target.end_line}`}
                  aria-label={`Citation ${n}: ${target.file_path} lines ${target.start_line} to ${target.end_line}`}
                >
                  {n}
                </button>
              );
            }
            return (
              <a href={href} target="_blank" rel="noreferrer noopener">
                {children}
              </a>
            );
          },
        }}
      >
        {linkCitations(text)}
      </ReactMarkdown>
    </div>
  );
}
