"""
Deploy Vertex AI Search (Discovery Engine) Datastore Schema
Usage: python deploy-search-index.py --project <project_id> --location <location> --datastore-id <id>
"""
import argparse
import json
from pathlib import Path

from google.cloud import discoveryengine_v1 as discoveryengine


def deploy_datastore(project_id: str, location: str, datastore_id: str, schema_file: str = "infra/gcp/vertex-search-schema.json"):
    """Deploy or update a Discovery Engine datastore schema"""

    schema_path = Path(schema_file)
    if not schema_path.exists():
        print(f"Error: Schema file not found: {schema_file}")
        return False

    with open(schema_path, "r") as f:
        schema_def = json.load(f)

    client = discoveryengine.SchemaServiceClient()

    # Build the schema resource name
    parent = f"projects/{project_id}/locations/{location}/dataStores/{datastore_id}"
    schema_name = f"{parent}/schemas/default_schema"

    schema = discoveryengine.Schema(
        name=schema_name,
        struct_schema=schema_def.get("schema", {}).get("structDefinition", {}),
    )

    print(f"Deploying schema to datastore '{datastore_id}' in project '{project_id}'...")

    try:
        # Try to update existing schema
        request = discoveryengine.UpdateSchemaRequest(schema=schema)
        operation = client.update_schema(request=request)
        result = operation.result()
        print(f"Schema updated successfully: {result.name}")
        return True
    except Exception as update_err:
        # If update fails, try to create
        print(f"Update failed ({update_err}), attempting create...")
        try:
            request = discoveryengine.CreateSchemaRequest(
                parent=parent,
                schema=schema,
                schema_id="default_schema",
            )
            operation = client.create_schema(request=request)
            result = operation.result()
            print(f"Schema created successfully: {result.name}")
            return True
        except Exception as create_err:
            print(f"Error: Failed to create schema: {create_err}")
            return False


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Deploy Vertex AI Search Datastore Schema")
    parser.add_argument("--project", required=True, help="GCP Project ID")
    parser.add_argument("--location", default="global", help="Datastore location (default: global)")
    parser.add_argument("--datastore-id", required=True, help="Discovery Engine datastore ID")
    parser.add_argument("--schema-file", default="infra/gcp/vertex-search-schema.json", help="Path to schema JSON file")

    args = parser.parse_args()

    success = deploy_datastore(args.project, args.location, args.datastore_id, args.schema_file)
    exit(0 if success else 1)
