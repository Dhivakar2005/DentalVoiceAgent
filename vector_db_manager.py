import os
import requests
import numpy as np
from pymongo import MongoClient
import certifi
from dotenv import load_dotenv
from typing import List, Dict, Any

import structlog

load_dotenv()

logger = structlog.get_logger(__name__)

class OllamaEmbeddingFunction:
    """Custom embedding function to use Ollama's embeddings API."""
    def __init__(self, model_name: str = "aya-expanse:8b", base_url: str = "http://localhost:11434"):
        self.model_name = model_name
        self.base_url = base_url

    def get_embedding(self, text: str) -> List[float]:
        try:
            resp = requests.post(
                f"{self.base_url}/api/embeddings",
                json={"model": self.model_name, "prompt": text},
                timeout=10
            )
            resp.raise_for_status()
            return resp.json()["embedding"]
        except Exception as e:
            logger.error("ollama_embedding_error", error=str(e))
            return [0.0] * 4096  # aya-expanse:8b (Llama 3 based) default size

class VectorDBManager:
    _instance = None # Singleton

    def __new__(cls, *args, **kwargs):
        if not cls._instance:
            cls._instance = super(VectorDBManager, cls).__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized: return
        
        # MongoDB Configuration
        self.mongo_uri = os.getenv("MONGO_URI", "mongodb+srv://dhikrish42:dhivs4321mdb@cluster.gyo49rj.mongodb.net/?appName=Cluster")
        self.db_name   = "dental_assistant"
        self.col_name  = "vector_faq"
        
        self.client = MongoClient(
            self.mongo_uri,
            tls=True,
            tlsCAFile=certifi.where()
        )
        self.db         = self.client[self.db_name]
        self.collection = self.db[self.col_name]
        
        self.embedding_fn = OllamaEmbeddingFunction()
        self._embedding_cache = {} # Simple LRU-style cache
        self._initialized = True

    def _get_cached_embedding(self, text: str) -> List[float]:
        """Simple cache wrapper for embeddings."""
        if text in self._embedding_cache:
            return self._embedding_cache[text]
        
        emb = self.embedding_fn.get_embedding(text)
        # Keep cache small (last 50 queries)
        if len(self._embedding_cache) > 50:
            self._embedding_cache.pop(next(iter(self._embedding_cache)))
        self._embedding_cache[text] = emb
        return emb

    def add_documents(self, documents: List[str], metadatas: List[Dict[str, Any]], ids: List[str]):
        """Store documents and their embeddings in MongoDB."""
        mongo_docs = []
        for i in range(len(ids)):
            emb = self.embedding_fn.get_embedding(documents[i])
            mongo_docs.append({
                "id": ids[i],
                "text": documents[i],
                "metadata": metadatas[i] if metadatas else {},
                "embedding": emb,
            })
        
        if mongo_docs:
            self.collection.insert_many(mongo_docs)
            logger.info("added_documents_to_mongodb", num_docs=len(mongo_docs))

    def query(self, text: str, n_results: int = 3) -> Dict[str, Any]:
        """Performs a vector search using Optimized NumPy Cosine Similarity."""
        query_emb = self._get_cached_embedding(text)
        
        # Fetch all records from MongoDB (with embeddings)
        cursor = self.collection.find({}, {"text": 1, "embedding": 1})
        all_records = list(cursor)
        if not all_records:
            return {"documents": [[]]}

        # 1. Extract texts and embeddings into arrays
        texts = [rec["text"] for rec in all_records if rec.get("embedding")]
        embs  = np.array([rec["embedding"] for rec in all_records if rec.get("embedding")])
        
        if len(embs) == 0:
            return {"documents": [[]]}

        # 2. Vectorized Cosine Similarity
        dot_products = np.dot(embs, query_emb)
        norm_query   = np.linalg.norm(query_emb)
        norm_targets = np.linalg.norm(embs, axis=1)
        
        # Avoid division by zero
        scores = dot_products / (norm_query * norm_targets + 1e-9)

        # 3. Sort and pick top results
        top_indices = np.argsort(scores)[::-1][:n_results]
        top_docs    = [texts[i] for i in top_indices]
        
        return {"documents": [top_docs]}

    def get_context(self, text: str, n_results: int = 3) -> str:
        """Helper to get a flat string of context for the LLM."""
        try:
            results = self.query(text, n_results=n_results)
            docs = results.get("documents", [[]])[0]
            if not docs: return ""
            
            context_parts = []
            for i, doc in enumerate(docs):
                context_parts.append(f"Result {i+1}: {doc}")
            return "\n\n".join(context_parts)
        except Exception as e:
            logger.error("context_retrieval_failed", error=str(e))
            return ""

if __name__ == "__main__":
    vdb = VectorDBManager()
    logger.info("vector_db_initialized")
