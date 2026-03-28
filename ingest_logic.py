import json
import os
from vector_db_manager import VectorDBManager

def ingest_from_logic_json(json_path: str = "d:/04_Others/Dental_Care/logic.json"):
    if not os.path.exists(json_path):
        print(f"[ERROR] {json_path} not found.")
        return

    with open(json_path, 'r', encoding='utf-8') as f:
        data = json.load(f)

    faq_database = data.get("faq_database", {})
    if not faq_database:
        print("[WARNING] No faq_database found in logic.json")
        return

    vdb = VectorDBManager()
    
    documents = []
    metadatas = []
    ids = []

    for key, entry in faq_database.items():
        # Combine keywords and answer for better retrieval
        keywords = ", ".join(entry.get("keywords", []))
        answer = entry.get("answer", "")
        
        doc_text = f"Question/Keywords: {keywords}\nAnswer: {answer}"
        
        documents.append(doc_text)
        metadatas.append({"category": key})
        ids.append(key)

    print(f"[INGEST] Adding {len(documents)} entries to Vector DB...")
    vdb.add_documents(documents, metadatas, ids)
    print("[SUCCESS] Ingestion complete.")

if __name__ == "__main__":
    ingest_from_logic_json()
