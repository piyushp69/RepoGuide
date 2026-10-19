import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Bot, CornerDownLeft, FileSearch, SearchX, ShieldAlert, Square, ThumbsDown, ThumbsUp, Trash2, UserRound } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { api, streamChat } from "../api/client";
import type { ChatMessage, Project, Source } from "../api/types";
import { useOpenCode } from "../components/CodeViewer";
import { InlineText, Markdown } from "../components/Markdown";
import { Badge, Button, EmptyState, ErrorBox, Modal, PageHeader, Spinner, cx } from "../components/ui";
import { useAppState } from "../state/AppState";

const EXAMPLES = [
  "What does this project do, and how is it structured?",
  "Where is the entry point and what happens at startup?",
  "How is configuration loaded?",
  "What are the main data models?",
];

interface Pending {
  question: string;
  text: string;
  sources: Source[];
  phase: "retrieving" | "generating";
  notice?: string;
}

function SourcesList({ sources }: { sources: Source[] }) {
  const openCode = useOpenCode();
  if (!sources.length) return null;
  return (
    <details className="mt-2 text-xs">
      <summary className="cursor-pointer text-muted">
        <FileSearch className="mr-1 inline size-3.5 align-text-bottom" aria-hidden />
        {sources.length} code excerpts were given to the model
      </summary>
      <ol className="mt-2 space-y-1">
        {sources.map((s) => (
          <li key={s.n}>
            <button
              className="flex w-full items-start gap-2 rounded px-1 py-0.5 text-left hover:bg-surface-2"
              onClick={() => openCode({ path: s.file_path, start: s.start_line, end: s.end_line, chunkId: s.chunk_id, title: s.symbol ?? undefined })}
            >
              <span className="mt-0.5 inline-flex h-4 min-w-4 items-center justify-center rounded bg-surface-2 px-1 font-mono text-[10px] text-ink-2">{s.n}</span>
              <span className="min-w-0">
                <span className="font-mono text-ink">{s.file_path}:{s.start_line}-{s.end_line}</span>
                {s.symbol && <span className="text-muted"> · {s.symbol_type} {s.symbol}</span>}
                {s.via && <span className="text-muted"> · graph: {s.via}</span>}
              </span>
            </button>
          </li>
        ))}
      </ol>
    </details>
  );
}

function FeedbackControls({ message, onSaved }: { message: ChatMessage; onSaved: (m: ChatMessage) => void }) {
  const [rating, setRating] = useState<1 | -1 | null>((message.feedback?.rating as 1 | -1 | undefined) ?? null);
  const [comment, setComment] = useState(message.feedback?.comment ?? "");
  const [editing, setEditing] = useState(false);
  const save = useMutation({
    mutationFn: (args: { rating: 1 | -1; comment?: string }) => api.feedback(message.id, args.rating, args.comment),
    onSuccess: (saved) => onSaved({ ...message, feedback: { rating: saved.rating, comment: saved.comment } }),
  });
  const choose = (value: 1 | -1) => {
    setRating(value);
    setEditing(true);
    save.mutate({ rating: value, comment: comment || undefined });
  };
  return (
    <div className="mt-2">
      <div className="flex flex-wrap items-center gap-1 text-xs text-muted">
        <span className="mr-1">Was this helpful?</span>
        <button
          className={cx("rounded p-1.5 hover:bg-surface-2", rating === 1 && "bg-good-soft text-good-ink")}
          aria-pressed={rating === 1} aria-label="Thumbs up" onClick={() => choose(1)}
        >
          <ThumbsUp className="size-3.5" />
        </button>
        <button
          className={cx("rounded p-1.5 hover:bg-surface-2", rating === -1 && "bg-bad-soft text-bad-ink")}
          aria-pressed={rating === -1} aria-label="Thumbs down" onClick={() => choose(-1)}
        >
          <ThumbsDown className="size-3.5" />
        </button>
        {rating !== null && !editing && (
          <button className="ml-1 underline" onClick={() => setEditing(true)}>
            {message.feedback?.comment ? "Edit comment" : "Add comment"}
          </button>
        )}
        {save.isSuccess && !editing && <span className="ml-1">Thanks — saved.</span>}
      </div>
      {editing && rating !== null && (
        <form
          className="mt-2 flex flex-col gap-2 sm:flex-row"
          onSubmit={(event) => {
            event.preventDefault();
            save.mutate({ rating, comment: comment.trim() || undefined }, { onSuccess: () => setEditing(false) });
          }}
        >
          <input
            className="h-8 min-w-0 flex-1 rounded-md border border-line-strong bg-surface px-2 text-sm"
            placeholder={rating === -1 ? "What was wrong or missing? (optional)" : "Optional comment"}
            value={comment}
            maxLength={2000}
            onChange={(event) => setComment(event.target.value)}
          />
          <div className="flex gap-2">
            <Button size="sm" type="submit" variant="primary" busy={save.isPending}>Save</Button>
            <Button size="sm" type="button" variant="ghost" onClick={() => setEditing(false)}>Close</Button>
          </div>
        </form>
      )}
      {save.error && <div className="mt-2"><ErrorBox error={save.error} /></div>}
    </div>
  );
}

function AssistantBubble({ message, onSaved }: { message: ChatMessage; onSaved: (m: ChatMessage) => void }) {
  return (
    <div className="flex gap-3">
      <div className="flex size-7 shrink-0 items-center justify-center rounded-full bg-accent-soft text-accent-ink"><Bot className="size-4" aria-hidden /></div>
      <div className="min-w-0 flex-1 rounded-xl border border-line bg-surface p-3">
        {message.grounded === 0 && (
          <div className="mb-2"><Badge tone="warn"><SearchX className="size-3" aria-hidden /> Not found in the indexed code</Badge></div>
        )}
        {message.grounded === -1 && (
          <div className="mb-2"><Badge tone="bad"><ShieldAlert className="size-3" aria-hidden /> Unverified: no citations</Badge></div>
        )}
        <Markdown text={message.content} sources={message.sources} citations={message.citations} />
        {message.warnings?.length > 0 && (
          <ul className="mt-2 space-y-1 rounded-md bg-warn-soft p-2 text-xs text-warn-ink">
            {message.warnings.map((warning) => <li key={warning}><InlineText text={warning} /></li>)}
          </ul>
        )}
        {message.citations.length > 0 && (
          <div className="mt-2 flex flex-wrap gap-1 text-xs text-muted">
            Cited:
            {message.citations.map((c) => (
              <span key={c.n} className="font-mono">[{c.n}] {c.file_path}:{c.start_line}-{c.end_line}</span>
            ))}
          </div>
        )}
        <SourcesList sources={message.sources} />
        {message.grounded !== null && <FeedbackControls message={message} onSaved={onSaved} />}
      </div>
    </div>
  );
}

function UserBubble({ text }: { text: string }) {
  return (
    <div className="flex justify-end gap-3">
      <div className="max-w-[85%] whitespace-pre-wrap rounded-xl bg-accent px-3 py-2 text-sm text-white">{text}</div>
      <div className="flex size-7 shrink-0 items-center justify-center rounded-full bg-surface-2 text-ink-2"><UserRound className="size-4" aria-hidden /></div>
    </div>
  );
}

export default function ChatPage({ project }: { project: Project }) {
  const { learner } = useAppState();
  const client = useQueryClient();
  const key = ["chat", project.id, learner];
  const history = useQuery({ queryKey: key, queryFn: () => api.chatHistory(project.id, learner) });
  const [input, setInput] = useState("");
  const [pending, setPending] = useState<Pending | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [confirmClear, setConfirmClear] = useState(false);
  const abort = useRef<AbortController | null>(null);
  const bottom = useRef<HTMLDivElement>(null);

  useEffect(() => {
    bottom.current?.scrollIntoView({ block: "end" });
  }, [history.data?.length, pending?.text, pending?.phase]);
  useEffect(() => () => abort.current?.abort(), []);

  const updateMessage = useCallback(
    (updated: ChatMessage) =>
      client.setQueryData<ChatMessage[]>(["chat", project.id, learner], (old) =>
        old?.map((m) => (m.id === updated.id ? updated : m)),
      ),
    [client, project.id, learner],
  );

  const ask = async (question: string) => {
    const text = question.trim();
    if (!text || pending) return;
    setError(null);
    setInput("");
    setPending({ question: text, text: "", sources: [], phase: "retrieving" });
    const controller = new AbortController();
    abort.current = controller;
    try {
      await streamChat(project.id, text, learner, (event) => {
        if (event.type === "sources") setPending((p) => p && { ...p, sources: event.sources, phase: "generating" });
        else if (event.type === "token") setPending((p) => p && { ...p, text: p.text + event.text });
        else if (event.type === "rewrite") setPending((p) => p && { ...p, text: "", notice: event.message });
        else if (event.type === "error") setError(event.message);
      }, controller.signal);
    } catch (err) {
      if ((err as Error).name !== "AbortError") setError((err as Error).message);
    } finally {
      abort.current = null;
      await client.invalidateQueries({ queryKey: key });
      setPending(null);
    }
  };

  const clear = useMutation({
    mutationFn: () => api.clearChat(project.id, learner),
    onSuccess: () => {
      setConfirmClear(false);
      client.invalidateQueries({ queryKey: key });
    },
  });

  const messages = history.data ?? [];
  const notIndexed = !project.chunk_count;

  return (
    <div className="flex min-h-[calc(100vh-9rem)] flex-col">
      <PageHeader
        title={`Chat · ${project.name}`}
        description={
          <>Answers come only from the indexed code and cite files and line ranges. Click a citation to open the code.
            {learner ? ` History is saved for ${learner}.` : " Choose a learner to keep a personal history."}</>
        }
        actions={messages.length > 0 && (
          <Button size="sm" variant="ghost" icon={<Trash2 className="size-3.5" />} onClick={() => setConfirmClear(true)}>
            Clear history
          </Button>
        )}
      />
      {notIndexed && <ErrorBox error="This project has not been indexed yet. Index it on the Projects page." />}
      <div className="flex-1 space-y-4 pb-4">
        {history.isLoading && <Spinner label="Loading history…" />}
        {history.error && <ErrorBox error={history.error} onRetry={() => history.refetch()} />}
        {!history.isLoading && messages.length === 0 && !pending && !notIndexed && (
          <EmptyState icon={<Bot className="size-8" />} title="Ask anything about this codebase">
            <div className="mt-3 flex flex-wrap justify-center gap-2">
              {EXAMPLES.map((example) => (
                <button key={example} className="rounded-full border border-line-strong px-3 py-1 text-xs text-ink-2 hover:bg-surface-2"
                  onClick={() => ask(example)}>
                  {example}
                </button>
              ))}
            </div>
          </EmptyState>
        )}
        {messages.map((message) =>
          message.role === "user" ? (
            <UserBubble key={message.id} text={message.content} />
          ) : (
            <AssistantBubble key={message.id} message={message} onSaved={updateMessage} />
          ),
        )}
        {pending && (
          <>
            <UserBubble text={pending.question} />
            <div className="flex gap-3">
              <div className="flex size-7 shrink-0 items-center justify-center rounded-full bg-accent-soft text-accent-ink"><Bot className="size-4" aria-hidden /></div>
              <div className="min-w-0 flex-1 rounded-xl border border-line bg-surface p-3" aria-live="polite">
                {pending.notice && <div className="mb-2"><Badge tone="warn">{pending.notice}</Badge></div>}
                {pending.text ? (
                  <Markdown text={pending.text} sources={pending.sources} />
                ) : (
                  <Spinner label={pending.phase === "retrieving" ? "Searching the code (vector + keyword + graph)…" : "Reading the code and writing an answer…"} />
                )}
                <SourcesList sources={pending.sources} />
              </div>
            </div>
          </>
        )}
        {error && <ErrorBox error={error} />}
        <div ref={bottom} />
      </div>
      <form
        className="sticky bottom-0 -mx-1 rounded-xl border border-line bg-surface p-2 shadow-sm"
        onSubmit={(event) => {
          event.preventDefault();
          ask(input);
        }}
      >
        <div className="flex items-end gap-2">
          <textarea
            className="max-h-40 min-h-[2.5rem] flex-1 resize-y rounded-md bg-transparent px-2 py-2 text-sm outline-none"
            placeholder={notIndexed ? "Index the project first" : "Ask about the code… (Enter to send, Shift+Enter for a new line)"}
            value={input}
            disabled={notIndexed}
            maxLength={4000}
            rows={1}
            onChange={(event) => setInput(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter" && !event.shiftKey) {
                event.preventDefault();
                ask(input);
              }
            }}
            aria-label="Question"
          />
          {pending ? (
            <Button type="button" icon={<Square className="size-3.5" />} onClick={() => abort.current?.abort()}>Stop</Button>
          ) : (
            <Button type="submit" variant="primary" icon={<CornerDownLeft className="size-4" />} disabled={!input.trim() || notIndexed}>
              Ask
            </Button>
          )}
        </div>
      </form>
      <Modal
        open={confirmClear}
        onClose={() => setConfirmClear(false)}
        title="Clear chat history?"
        footer={
          <>
            <Button onClick={() => setConfirmClear(false)}>Cancel</Button>
            <Button variant="danger" busy={clear.isPending} onClick={() => clear.mutate()}>Clear</Button>
          </>
        }
      >
        <p className="text-sm text-ink-2">
          Deletes {learner ? `${learner}'s` : "the guest"} conversation for this project. Feedback already given is kept.
        </p>
      </Modal>
    </div>
  );
}
