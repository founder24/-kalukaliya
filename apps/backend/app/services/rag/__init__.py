"""
Syrabit RAG Pipeline

Stack:
  Embeddings  — CF Workers AI @cf/baai/bge-m3  (1024-dim, multilingual EN+AS)
  LLM         — Sarvam AI sarvam-30b / sarvam-105b (EN + AS)
  Vector Store — MongoDB Atlas Vector Search (1024-dim cosine index)

Modules:
  cleaner    — Unicode normalization, boilerplate removal, language detection
  chunker    — Source-type-aware splitting (notes/definition/qa_pair/pyq)
  ingestion  — Full ingest pipeline: clean → chunk → embed → upsert to Atlas
  retrieval  — Metadata-filtered vector retrieval for chat RAG
  evaluator  — RAGAS-compatible offline evaluation harness
"""
