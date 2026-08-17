"""
09_EMBEDDINGS (part 2) — turn context text into vectors.

Uses fastembed (ONNX runtime, tiny install footprint) locally so the MVP
has zero API cost/latency. The model defaults to all-MiniLM-L6-v2 (384 dims)
— the same model used everywhere else in this project, so vectors are
comparable. Swap the model name in .env / 13_CONFIG/CONFIG.yaml without
touching this file (keep the VECTOR(384) column in sync with the model dims).
"""
import os

_model = None


def get_model():
    global _model
    if _model is None:
        from fastembed import TextEmbedding
        model_name = os.getenv(
            "EMBEDDING_MODEL", "sentence-transformers/all-MiniLM-L6-v2")
        _model = TextEmbedding(model_name=model_name)
    return _model


def embed_texts(texts: list[str]) -> list[list[float]]:
    model = get_model()
    return [vec.tolist() for vec in model.embed(texts, batch_size=32)]


if __name__ == "__main__":
    print("Loading model (first run downloads weights)...")
    vecs = embed_texts(["IIT Delhi has strong industry collaboration."])
    print(f"Embedding dimension: {len(vecs[0])}")
