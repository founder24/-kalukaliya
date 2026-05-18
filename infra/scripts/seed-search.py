"""
Seed Azure Search Index with Educational Content
Usage: python seed-search.py --endpoint <endpoint> --key <admin_key> --data-dir <path_to_docs>
"""
import argparse
import json
import requests
import hashlib
from pathlib import Path
from typing import List, Dict


def chunk_text(text: str, max_tokens: int = 512) -> List[str]:
    """Split text into chunks of approximately max_tokens"""
    # Simple character-based chunking (replace with token-based for production)
    chars_per_token = 4  # Approximate
    max_chars = max_tokens * chars_per_token
    
    chunks = []
    for i in range(0, len(text), max_chars):
        chunk = text[i:i + max_chars]
        if len(chunk) > 100:  # Only add meaningful chunks
            chunks.append(chunk)
    return chunks


def generate_embedding(text: str, endpoint: str, admin_key: str) -> List[float]:
    """Generate embedding using Azure OpenAI"""
    # This is a placeholder - in production, call Azure OpenAI embedding API
    # For now, return a dummy vector
    import random
    return [random.uniform(-1, 1) for _ in range(1536)]


def upload_documents(endpoint: str, admin_key: str, index_name: str, documents: List[Dict]):
    """Upload documents to Azure Search index"""
    url = f"{endpoint}/indexes/{index_name}/docs/index?api-version=2023-10-01-Preview"
    
    headers = {
        "Content-Type": "application/json",
        "api-key": admin_key
    }
    
    payload = {"value": documents}
    
    response = requests.post(url, json=payload, headers=headers)
    
    if response.status_code in [200, 201]:
        print(f"✓ Uploaded {len(documents)} documents successfully!")
        return True
    else:
        print(f"✗ Failed to upload documents: {response.status_code}")
        print(f"Response: {response.text}")
        return False


def seed_index(endpoint: str, admin_key: str, index_name: str, data_dir: str):
    """Seed Azure Search index with educational content"""
    
    data_path = Path(data_dir)
    if not data_path.exists():
        print(f"✗ Data directory not found: {data_dir}")
        return False
    
    documents = []
    
    # Process all text files in the data directory
    for file_path in data_path.glob("**/*.txt"):
        print(f"Processing {file_path}...")
        
        with open(file_path, "r", encoding="utf-8") as f:
            content = f.read()
        
        chunks = chunk_text(content)
        
        for i, chunk in enumerate(chunks):
            doc_id = hashlib.md5(f"{file_path}:{i}".encode()).hexdigest()
            
            # Generate embedding (placeholder)
            embedding = generate_embedding(chunk, endpoint, admin_key)
            
            doc = {
                "id": doc_id,
                "title": file_path.stem,
                "content": chunk,
                "content_vector": embedding,
                "language": "as",  # Assamese
                "tier_access": "free",
                "source_url": f"file://{file_path}",
                "last_updated": "2024-01-01T00:00:00Z"
            }
            documents.append(doc)
    
    if not documents:
        print("✗ No documents found to index")
        return False
    
    print(f"Uploading {len(documents)} document chunks...")
    return upload_documents(endpoint, admin_key, index_name, documents)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Seed Azure Search Index with Educational Content")
    parser.add_argument("--endpoint", required=True, help="Azure Search endpoint URL")
    parser.add_argument("--key", required=True, help="Azure Search Admin Key")
    parser.add_argument("--index-name", default="syrabit-edu-index", help="Azure Search index name")
    parser.add_argument("--data-dir", required=True, help="Path to directory containing educational content")
    
    args = parser.parse_args()
    
    success = seed_index(args.endpoint, args.key, args.index_name, args.data_dir)
    exit(0 if success else 1)
