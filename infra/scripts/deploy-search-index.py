"""
Deploy Azure Search Index Schema
Usage: python deploy-search-index.py --endpoint <endpoint> --key <admin_key>
"""
import argparse
import json
import requests
from pathlib import Path


def deploy_index(endpoint: str, admin_key: str, index_file: str = "infra/azure/search-index.json"):
    """Deploy or update Azure Search index schema"""
    
    # Load index definition
    index_path = Path(index_file)
    if not index_path.exists():
        print(f"✗ Index file not found: {index_file}")
        return False
    
    with open(index_path, "r") as f:
        index_def = json.load(f)
    
    index_name = index_def["name"]
    url = f"{endpoint}/indexes/{index_name}?api-version=2023-10-01-Preview"
    
    headers = {
        "Content-Type": "application/json",
        "api-key": admin_key
    }
    
    print(f"Deploying index '{index_name}' to {endpoint}...")
    
    # Try to create or update the index
    response = requests.put(url, json=index_def, headers=headers)
    
    if response.status_code in [200, 201]:
        print(f"✓ Index '{index_name}' deployed successfully!")
        return True
    else:
        print(f"✗ Failed to deploy index: {response.status_code}")
        print(f"Response: {response.text}")
        return False


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Deploy Azure Search Index")
    parser.add_argument("--endpoint", required=True, help="Azure Search endpoint URL")
    parser.add_argument("--key", required=True, help="Azure Search Admin Key")
    parser.add_argument("--index-file", default="infra/azure/search-index.json", help="Path to index JSON file")
    
    args = parser.parse_args()
    
    success = deploy_index(args.endpoint, args.key, args.index_file)
    exit(0 if success else 1)
