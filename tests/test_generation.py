from dataclasses import dataclass

from rag.generation.base import GenerationResult
from rag.generation.citations import validate_citations
from rag.generation.context import build_context
from rag.generation.service import QueryService
from rag.retrieval.dense import RetrievedChunk


@dataclass
class FakeRetriever:
    results: list

    def retrieve(self, index_id, query, top_k=5):
        return self.results[:top_k]


class FakeLLM:
    def __init__(self, text):
        self.text = text
        self.calls = 0

    def generate(self, *, question, context):
        self.calls += 1
        return GenerationResult(self.text, 12, 5)


def chunk(chunk_id="c1", text="def dispatch_request(): pass"):
    return {
        "chunk_id": chunk_id,
        "file_path": "src/app.py",
        "qualname": "dispatch_request",
        "symbol_kind": "function",
        "start_line": 10,
        "end_line": 11,
        "text": text,
    }


def test_context_assigns_stable_refs_and_source_metadata():
    results = [
        RetrievedChunk(chunk(), 0.91, 1),
        RetrievedChunk(chunk("c2", "return response"), 0.82, 2),
    ]
    context = build_context(results)

    assert "[1] src/app.py:10-11 (dispatch_request)" in context.text
    assert "[2] src/app.py:10-11 (dispatch_request)" in context.text
    assert [source.ref_id for source in context.sources] == [1, 2]


def test_citation_validator_discards_unknown_refs_and_builds_contract_shape():
    source = build_context([RetrievedChunk(chunk(), 0.9, 1)]).sources
    answer, citations = validate_citations("Found here [1], not [99].", source)

    assert answer == "Found here [1], not ."
    assert len(citations) == 1
    assert set(citations[0]) == {
        "ref_id",
        "file_path",
        "symbol",
        "symbol_kind",
        "start_line",
        "end_line",
        "snippet",
        "score",
    }


def test_uncited_or_only_invalid_answer_fails_closed():
    source = build_context([RetrievedChunk(chunk(), 0.9, 1)]).sources
    answer, citations = validate_citations("Unsupported answer [42].", source)

    assert "not find enough evidence" in answer
    assert citations == []


def test_query_service_uses_dense_retrieval_and_reports_usage_and_debug():
    retriever = FakeRetriever([RetrievedChunk(chunk(), 0.9, 1)])
    llm = FakeLLM("The function dispatches the request [1].")
    result = QueryService(retriever, llm).query(
        "idx_demo", "What does it do?", debug=True
    )

    assert result.answer.endswith("[1].")
    assert result.citations[0]["ref_id"] == 1
    assert result.usage == {"input_tokens": 12, "output_tokens": 5}
    assert result.debug["mode"] == "dense"
    assert result.debug["retrieved"][0]["used_in_context"] is True


def test_empty_retrieval_does_not_call_llm():
    llm = FakeLLM("should not be called")
    result = QueryService(FakeRetriever([]), llm).query("idx_demo", "question")

    assert llm.calls == 0
    assert result.citations == []
