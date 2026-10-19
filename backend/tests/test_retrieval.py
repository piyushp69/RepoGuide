"""Hybrid retrieval, incremental indexing, context building and citation parsing."""

from __future__ import annotations

from repoguide.index.bm25 import BM25Index, tokenize
from repoguide.jobs import PrintReporter
from repoguide.qa.citations import (align_citations, build_context, is_not_found, parse_citations, path_mentions,
                                    unknown_paths)
from repoguide.retrieval.hybrid import RetrievedChunk, reciprocal_rank_fusion


def test_tokenizer_splits_identifiers_and_stems():
    tokens = tokenize("def computeOrderTotal(discount_codes): DISCOUNT_CODES applied")
    assert "computeordertotal" in tokens and "comput" in tokens and "order" in tokens and "total" in tokens
    assert "discount_codes" in tokens and tokens.count("discount") >= 2 and "code" in tokens
    assert "appli" in tokens  # "applied" stems like "apply"
    assert "def" not in tokens and "the" not in tokenize("the")


def test_bm25_ranks_matching_chunk_first():
    index = BM25Index(["a", "b", "c"], ["def apply_discount(amount, code): ...", "def calculate_tax(amount)",
                                        "class Cart: subtotal"])
    assert index.search("how are discount codes applied", 3)[0][0] == "a"
    assert index.search("zzz unknown", 3) == []


def test_reciprocal_rank_fusion_merges_and_dedupes():
    lists = {"vector": ["a", "b", "c"], "bm25": ["b", "d", "a", "b"], "symbol": []}
    fused = reciprocal_rank_fusion(lists, {"vector": 1.0, "bm25": 1.0, "symbol": 1.0})
    ids = [item for item, _, _ in fused]
    assert ids[:2] == ["b", "a"] and set(ids) == {"a", "b", "c", "d"} and len(ids) == 4
    ranks = {item: r for item, _, r in fused}
    assert ranks["b"] == {"vector": 2, "bm25": 1}
    # Production weights favour the vector ranking when the two retrievers disagree.
    weighted = [item for item, _, _ in reciprocal_rank_fusion(lists)]
    assert weighted[0] == "a"


def test_hybrid_retrieval_on_sample_repo(services, sample_project):
    question = "How is the final order total calculated?"
    keyword = services.retriever.retrieve(sample_project, question, mode="bm25", expand=False)
    assert keyword.seeds[0].symbol == "compute_order_total"
    # The fake embedder is a crude bag of words, so for hybrid only check that both retrievers
    # contributed and fusion kept the chunk among the seeds; real ranking quality is measured by
    # the evaluation harness with the real models.
    result = services.retriever.retrieve(sample_project, question)
    seeds = {(c.file_path, c.symbol): c for c in result.seeds}
    target = seeds.get(("backend/services/pricing.py", "compute_order_total"))
    assert target is not None and target.ranks["bm25"] == 1 and "vector" in target.ranks
    assert result.seed_count == services.settings.retrieval_top_k
    assert [c.score for c in result.seeds] == sorted((c.score for c in result.seeds), reverse=True)
    # Graph expansion adds the functions compute_order_total calls, with the reason.
    neighbours = {c.symbol: c.via for c in result.chunks[result.seed_count:]}
    assert neighbours.get("apply_discount") == "called by compute_order_total" or \
        any(c.symbol == "apply_discount" for c in result.seeds)
    ids = [c.id for c in result.chunks]
    assert len(ids) == len(set(ids))  # deduplicated


def test_symbol_lookup_finds_named_identifiers(services, sample_project):
    result = services.retriever.retrieve(sample_project, "What does `verify_token` return?", expand=False)
    assert result.seeds[0].symbol == "verify_token"
    result = services.retriever.retrieve(sample_project, "explain Cart.subtotalCents", expand=False)
    assert result.seeds[0].symbol == "Cart"  # the small class chunk containing the method
    result = services.retriever.retrieve(sample_project, "what is in cart.ts", expand=False)
    assert result.seeds[0].file_path == "frontend/src/cart.ts"


def test_focus_query_drops_self_references(services, sample_project):
    services.db.execute("UPDATE projects SET name = 'ShopLite (sample)' WHERE id = ?", (sample_project,))
    focus = services.retriever.focus_query
    assert focus(sample_project, "How does ShopLite compute sales tax?") == "How does compute sales tax?"
    assert focus(sample_project, "What does this project do with discount codes?") == "What does do with discount codes?"
    assert focus(sample_project, "Where is the codebase's config loaded?") == "Where is 's config loaded?"
    assert focus(sample_project, "What is ShopLite?") == "What is ShopLite?"  # too little left: keep the original
    assert focus(sample_project, "How is the project deleted?") == "How is the project deleted?"  # ambiguous: kept
    result = services.retriever.retrieve(sample_project, "How does ShopLite compute sales tax?", expand=False)
    assert result.search_query == "How does compute sales tax?" and result.query.startswith("How does ShopLite")


def test_retrieval_modes(services, sample_project):
    for mode in ("vector", "bm25", "hybrid"):
        result = services.retriever.retrieve(sample_project, "reserve stock for an order", mode=mode, expand=False)
        assert result.chunks, mode
        assert all(set(c.ranks) <= {"vector", "bm25", "symbol"} for c in result.chunks)


def test_incremental_indexing(services, sample_project):
    root = services.settings.sample_repo_dir
    embedder = services.embedder
    before = services.db.scalar("SELECT COUNT(*) FROM chunks WHERE project_id = ?", (sample_project,))
    assert services.store.count(sample_project) == before

    # No changes: nothing is re-parsed or re-embedded.
    calls_before = embedder.document_calls
    stats = services.indexer.index_project(sample_project, PrintReporter())
    assert stats["unchanged"] == 15 and stats["added"] == stats["changed"] == stats["removed"] == 0
    assert stats["embedded"] == 0 and embedder.document_calls == calls_before

    # Appending a function changes one file; only its new chunk is embedded.
    pricing = root / "backend" / "services" / "pricing.py"
    pricing.write_text(pricing.read_text(encoding="utf-8") + "\n\ndef gift_wrap_fee(items):\n    return 250\n",
                       encoding="utf-8")
    stats = services.indexer.index_project(sample_project, PrintReporter())
    assert stats["changed"] == 1 and stats["unchanged"] == 14
    assert stats["embedded"] == 1  # unchanged chunks of the edited file keep their vectors
    hit = services.retriever.retrieve(sample_project, "gift wrap fee", expand=False).seeds[0]
    assert hit.symbol == "gift_wrap_fee"

    # Deleting a file removes its chunks from SQLite and from the vector store.
    (root / "backend" / "services" / "notifications.py").unlink()
    stats = services.indexer.index_project(sample_project, PrintReporter())
    assert stats["removed"] == 1
    remaining = services.db.scalar("SELECT COUNT(*) FROM chunks WHERE project_id = ? AND file_path LIKE ?",
                                   (sample_project, "%notifications.py"))
    assert remaining == 0
    assert services.store.count(sample_project) == services.db.scalar(
        "SELECT COUNT(*) FROM chunks WHERE project_id = ?", (sample_project,))


def _chunk(n: int, path: str = "a.py", start: int = 1, end: int = 3, content: str = "x = 1\ny = 2\nz = 3"):
    return RetrievedChunk(id=f"c{n}", file_path=path, start_line=start, end_line=end, symbol=f"s{n}",
                          symbol_type="function", language="python", content=content)


def test_context_budget_trims_by_whole_lines():
    long = "\n".join(f"line_{i} = {i}" for i in range(400))
    blocks = build_context([_chunk(1), _chunk(2, start=10, end=409, content=long)], char_budget=2000)
    assert [b.number for b in blocks] == [1, 2]
    assert blocks[1].start_line == 10 and blocks[1].end_line < 409
    assert blocks[1].content == "\n".join(long.split("\n")[: blocks[1].end_line - 10 + 1])


def test_citation_parsing():
    blocks = build_context([_chunk(1), _chunk(2, path="b.py"), _chunk(3, path="c.py")], char_budget=10_000)
    answer = ("Totals are computed in `pricing.py` [1][2]. See also [2, 3] and [1-3]. "
              "Not a citation: `items[0]` or ```\nx = arr[2]\n```. Bad ref [9].")
    citations, invalid = parse_citations(answer, blocks)
    assert [c["n"] for c in citations] == [1, 2, 3]
    assert citations[1]["file_path"] == "b.py" and citations[0]["chunk_id"] == "c1"
    assert invalid == [9]
    assert path_mentions("see backend/app.py:12-20 and models.py") == [
        {"path": "backend/app.py", "start_line": 12, "end_line": 20},
        {"path": "models.py", "start_line": None, "end_line": None},
    ]


def test_align_citations_repoints_slipped_numbers():
    cancel = _chunk(4, path="frontend/src/api.ts", start=53, end=55,
                    content="export function cancelOrder(orderId: number) {\n  return request(`/orders/${orderId}/cancel`);\n}")
    place = _chunk(5, path="frontend/src/api.ts", start=45, end=51,
                   content="export function placeOrder(email: string) {\n  return request('/orders');\n}")
    cancel.symbol, place.symbol = "cancelOrder", "placeOrder"
    blocks = build_context([_chunk(1), _chunk(2), _chunk(3), cancel, place], char_budget=10_000)
    answer = ("The frontend uses the `placeOrder` function in `frontend/src/api.ts` [4]. It posts JSON.\n"
              "Cancelling goes through `cancelOrder` [4]. Shared code: `request` [4].\n"
              "```ts\nplaceOrder() // [4]\n```")
    fixed, notes = align_citations(answer, blocks)
    assert fixed.splitlines()[0] == "The frontend uses the `placeOrder` function in `frontend/src/api.ts` [5]. It posts JSON."
    assert "`cancelOrder` [4]" in fixed  # correct citation untouched
    assert "`request` [4]" in fixed  # the cited code mentions request(): untouched
    assert "placeOrder() // [4]" in fixed  # code blocks are never rewritten
    assert notes == ["Citation [4] was changed to [5] to match `placeOrder` named in the sentence."]
    # Ambiguous (two excerpts define the name) or unnamed sentences are left alone.
    twin = _chunk(6, path="b.ts", content="function placeOrder() {}")
    twin.symbol = "placeOrder"
    blocks = build_context([_chunk(1), _chunk(2), _chunk(3), cancel, place, twin], char_budget=10_000)
    assert align_citations("Uses `placeOrder` [4].", blocks)[1] == []
    assert align_citations("It sends the order [4].", blocks)[1] == []


def test_unknown_path_mentions():
    known = ["backend/repoguide/index/indexer.py", ".github/workflows/ci.yml"]
    answer = ("See `backend/repoguide/indexer.py` and repoguide/index/indexer.py, plus .github/workflows/ci.yml. "
              "Built with Node.js and settings.py.")
    assert unknown_paths(answer, known) == ["backend/repoguide/indexer.py"]


def test_uncited_draft_is_rewritten(services, sample_project, monkeypatch):
    calls = []
    real = services.llm.complete

    def first_draft_uncited(messages, temperature=None, num_predict=None):
        calls.append((temperature, messages[-1]["content"]))
        if len(calls) == 1:
            return "Totals use the file's modification time."  # no citation: must trigger the rewrite
        return real(messages)

    monkeypatch.setattr(services.llm, "complete", first_draft_uncited)
    events = list(services.qa.answer_stream(sample_project, "How is the order total calculated?"))
    kinds = [e["type"] for e in events]
    assert "rewrite" in kinds and kinds[-1] == "done"
    final = events[-1]["message"]
    assert final["citations"] and final["grounded"] == 1 and "modification time" not in final["content"]
    assert [t for t, _ in calls] == [services.settings.qa_temperature] * 2
    first_prompt, rewrite_prompt = calls[0][1], calls[1][1]
    assert "find the excerpt lines" in first_prompt  # evidence-first instruction
    assert "rejected" in rewrite_prompt and "modification time" not in rewrite_prompt  # the draft is not shown again

    monkeypatch.setattr(services.llm, "complete", lambda *a, **k: "It probably uses timestamps in backend/x/indexer.py.")
    final = services.qa.answer(sample_project, "How is the order total calculated?")
    assert final["grounded"] == -1 and not final["citations"]
    assert any("does not cite" in w for w in final["warnings"])
    assert any("backend/x/indexer.py" in w for w in final["warnings"])
    outcome = services.qa.run_once(sample_project, "How is the order total calculated?")
    assert outcome["rewritten"] and not outcome["citations"]


def test_not_found_detection():
    assert is_not_found("I could not find this in the indexed code. The closest is X [1].")
    assert is_not_found("The provided excerpts do not contain any Stripe integration.")
    assert not is_not_found("The `apply_discount` function does not include free shipping; it only applies a "
                            "percentage [1].")
