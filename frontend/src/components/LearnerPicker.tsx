import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { UserRound } from "lucide-react";
import { useState } from "react";
import { api } from "../api/client";
import { useAppState } from "../state/AppState";
import { Button, ErrorBox, Modal } from "./ui";

/** No real authentication: learners pick (or add) their name, which is stored in this browser. */
export function LearnerPicker() {
  const { learner, setLearner } = useAppState();
  const [open, setOpen] = useState(false);
  const [name, setName] = useState("");
  const client = useQueryClient();
  const learners = useQuery({ queryKey: ["learners"], queryFn: api.learners });
  const add = useMutation({
    mutationFn: (value: string) => api.addLearner(value),
    onSuccess: (created) => {
      client.invalidateQueries({ queryKey: ["learners"] });
      setLearner(created.name);
      setName("");
      setOpen(false);
    },
  });

  return (
    <>
      <button
        className="flex h-8 items-center gap-1.5 rounded-md border border-line-strong bg-surface px-2.5 text-sm text-ink hover:bg-surface-2"
        onClick={() => setOpen(true)}
        title="Choose who is learning (no password needed)"
      >
        <UserRound className="size-4 text-muted" aria-hidden />
        <span className="max-w-[8rem] truncate">{learner ?? "Choose learner"}</span>
      </button>
      <Modal open={open} onClose={() => setOpen(false)} title="Who is learning?">
        <div className="space-y-4">
          <p className="text-sm text-muted">
            Progress, quiz scores and chat history are saved per learner. There is no login: pick your name or add it.
          </p>
          {learners.data && learners.data.length > 0 && (
            <ul className="flex flex-wrap gap-2">
              {learners.data.map((l) => (
                <li key={l.id}>
                  <button
                    className={`rounded-full border px-3 py-1 text-sm ${l.name === learner ? "border-accent bg-accent-soft text-accent-ink" : "border-line-strong hover:bg-surface-2"}`}
                    onClick={() => {
                      setLearner(l.name);
                      setOpen(false);
                    }}
                  >
                    {l.name}
                  </button>
                </li>
              ))}
            </ul>
          )}
          <form
            className="flex gap-2"
            onSubmit={(event) => {
              event.preventDefault();
              if (name.trim()) add.mutate(name.trim());
            }}
          >
            <input
              className="h-9 min-w-0 flex-1 rounded-md border border-line-strong bg-surface px-3 text-sm"
              placeholder="Your name"
              value={name}
              maxLength={60}
              onChange={(event) => setName(event.target.value)}
              aria-label="New learner name"
            />
            <Button variant="primary" type="submit" busy={add.isPending} disabled={!name.trim()}>
              Add
            </Button>
          </form>
          {add.error && <ErrorBox error={add.error} />}
          {learner && (
            <button
              className="text-sm text-muted underline"
              onClick={() => {
                setLearner(null);
                setOpen(false);
              }}
            >
              Continue as guest (no progress tracking)
            </button>
          )}
        </div>
      </Modal>
    </>
  );
}
