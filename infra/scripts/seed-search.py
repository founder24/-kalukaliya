"""
Seed Vertex AI Search (Discovery Engine) Datastore with Educational Content
Usage:
  python seed-search.py --project <project_id> --datastore-id <id> --data-dir <path>
  python seed-search.py --project <project_id> --datastore-id <id> --sample
"""
import argparse
import hashlib
import json
from pathlib import Path
from typing import List

from google.cloud import discoveryengine_v1 as discoveryengine


# Built-in sample educational content for initial seeding
SAMPLE_DOCUMENTS = [
    {
        "title": "Mathematics - Basic Arithmetic and Algebra",
        "content": (
            "Mathematics is the foundation of logical thinking. Basic arithmetic includes "
            "addition, subtraction, multiplication, and division. For example, 25 + 17 = 42, "
            "and 8 x 7 = 56. Algebra introduces variables to represent unknown values. "
            "A simple equation like 2x + 5 = 15 can be solved by subtracting 5 from both sides "
            "to get 2x = 10, then dividing by 2 to find x = 5. Fractions represent parts of a "
            "whole: 1/2 + 1/4 = 3/4. Understanding place value helps with larger numbers: "
            "the number 3,456 has 3 thousands, 4 hundreds, 5 tens, and 6 ones. "
            "Multiplication tables are essential building blocks. The distributive property "
            "states that a(b + c) = ab + ac. These concepts form the basis for more advanced "
            "mathematics including geometry, trigonometry, and calculus."
        ),
        "language": "en",
        "subject": "mathematics",
    },
    {
        "title": "Science - Physics and Chemistry Basics",
        "content": (
            "Science helps us understand the natural world. In physics, Newton's three laws "
            "of motion describe how objects move. The first law states that an object at rest "
            "stays at rest unless acted upon by a force. The second law relates force, mass, "
            "and acceleration: F = ma. The third law says every action has an equal and opposite "
            "reaction. In chemistry, matter is made of atoms, which combine to form molecules. "
            "Water (H2O) is made of two hydrogen atoms and one oxygen atom. The periodic table "
            "organizes elements by atomic number. States of matter include solid, liquid, and gas. "
            "Energy can be converted between forms but cannot be created or destroyed (conservation "
            "of energy). Light travels at approximately 300,000 km/s. Gravity pulls objects "
            "toward Earth with an acceleration of 9.8 m/s squared."
        ),
        "language": "en",
        "subject": "science",
    },
    {
        "title": "English - Grammar and Vocabulary",
        "content": (
            "English grammar provides the rules for constructing sentences. A sentence must have "
            "a subject and a predicate. Nouns name people, places, or things. Verbs express actions "
            "or states of being. Adjectives describe nouns, while adverbs modify verbs. "
            "Tenses indicate when an action occurs: past (walked), present (walk), future (will walk). "
            "Pronouns replace nouns to avoid repetition: he, she, it, they. "
            "Punctuation marks include periods, commas, question marks, and exclamation points. "
            "A paragraph is a group of related sentences about one main idea. "
            "Common prefixes include un- (not), re- (again), and pre- (before). "
            "Common suffixes include -tion (action), -ful (full of), and -less (without). "
            "Reading comprehension involves understanding main ideas, supporting details, "
            "and making inferences from text."
        ),
        "language": "en",
        "subject": "english",
    },
    {
        "title": "Assamese Language - Basic Phrases and Letters",
        "content": (
            "Assamese (অসমীয়া) is the official language of Assam, spoken by millions of people. "
            "The Assamese script is derived from the ancient Brahmi script. Basic greetings include: "
            "নমস্কাৰ (Namaskar - Hello), আপোনাৰ নাম কি? (Aponar naam ki? - What is your name?), "
            "মোৰ নাম... (Mor naam... - My name is...), ধন্যবাদ (Dhanyabad - Thank you), "
            "আপুনি কেনে আছে? (Apuni kene aase? - How are you?). "
            "The Assamese alphabet has vowels (স্বৰবৰ্ণ) and consonants (ব্যঞ্জনবৰ্ণ). "
            "Numbers: এক (1), দুই (2), তিনি (3), চাৰি (4), পাঁচ (5). "
            "Days of the week: দেওবাৰ (Sunday), সোমবাৰ (Monday), মঙ্গলবাৰ (Tuesday). "
            "Assamese literature has a rich tradition dating back to the 14th century with "
            "works like Madhav Kandali's translation of the Ramayana."
        ),
        "language": "as",
        "subject": "assamese",
    },
    {
        "title": "History - Assam History",
        "content": (
            "Assam has a rich and ancient history. The Ahom dynasty ruled Assam for nearly "
            "600 years (1228-1826), making it one of the longest-ruling dynasties in Indian history. "
            "The Ahoms came from present-day Myanmar and established their kingdom in the Brahmaputra "
            "valley. They successfully resisted Mughal invasions, notably in the Battle of Saraighat "
            "(1671) led by Lachit Borphukan. The Koch Kingdom flourished in western Assam during "
            "the 16th century. The Treaty of Yandabo (1826) brought Assam under British control "
            "after the First Anglo-Burmese War. During the freedom struggle, Assam played an "
            "important role. Maniram Dewan was one of the first martyrs. Gopinath Bordoloi "
            "was instrumental in keeping Assam as part of India during partition. "
            "The ancient Kamakhya Temple on Nilachal Hill is one of the oldest Shakti Peethas, "
            "reflecting the deep cultural and religious heritage of the region."
        ),
        "language": "en",
        "subject": "history",
    },
]


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


def seed_from_sample(project_id: str, location: str, datastore_id: str):
    """Seed a Discovery Engine datastore with built-in sample educational content"""

    client = discoveryengine.DocumentServiceClient()
    parent = f"projects/{project_id}/locations/{location}/dataStores/{datastore_id}/branches/default_branch"

    documents_imported = 0

    for doc_data in SAMPLE_DOCUMENTS:
        print(f"Processing sample: {doc_data['title']}...")

        chunks = chunk_text(doc_data["content"])

        for i, chunk in enumerate(chunks):
            doc_id = hashlib.md5(f"sample:{doc_data['title']}:{i}".encode()).hexdigest()

            document = discoveryengine.Document(
                id=doc_id,
                struct_data={
                    "id": doc_id,
                    "title": doc_data["title"],
                    "content": chunk,
                    "language": doc_data["language"],
                    "subject": doc_data["subject"],
                    "tier_access": "free",
                    "source_url": f"sample://{doc_data['subject']}",
                    "last_updated": "2024-01-01T00:00:00Z",
                },
            )

            try:
                request = discoveryengine.CreateDocumentRequest(
                    parent=parent,
                    document=document,
                    document_id=doc_id,
                )
                client.create_document(request=request)
                documents_imported += 1
            except Exception as e:
                # Try update if document already exists
                try:
                    document.name = f"{parent}/documents/{doc_id}"
                    request = discoveryengine.UpdateDocumentRequest(
                        document=document,
                        allow_missing=True,
                    )
                    client.update_document(request=request)
                    documents_imported += 1
                except Exception as update_err:
                    print(f"  Warning: Failed to import chunk {i} of {doc_data['title']}: {update_err}")

    if documents_imported == 0:
        print("Error: No documents were imported")
        return False

    print(f"Successfully imported {documents_imported} sample document chunks")
    return True


def seed_from_directory(project_id: str, location: str, datastore_id: str, data_dir: str):
    """Seed a Discovery Engine datastore with content from a directory"""

    data_path = Path(data_dir)
    if not data_path.exists():
        print(f"Error: Data directory not found: {data_dir}")
        return False

    client = discoveryengine.DocumentServiceClient()
    parent = f"projects/{project_id}/locations/{location}/dataStores/{datastore_id}/branches/default_branch"

    documents_imported = 0

    # Process all text files in the data directory
    for file_path in data_path.glob("**/*.txt"):
        print(f"Processing {file_path}...")

        with open(file_path, "r", encoding="utf-8") as f:
            content = f.read()

        chunks = chunk_text(content)

        for i, chunk in enumerate(chunks):
            doc_id = hashlib.md5(f"{file_path}:{i}".encode()).hexdigest()

            document = discoveryengine.Document(
                id=doc_id,
                struct_data={
                    "id": doc_id,
                    "title": file_path.stem,
                    "content": chunk,
                    "language": "as",  # Assamese
                    "tier_access": "free",
                    "source_url": f"file://{file_path}",
                    "last_updated": "2024-01-01T00:00:00Z",
                },
            )

            try:
                request = discoveryengine.CreateDocumentRequest(
                    parent=parent,
                    document=document,
                    document_id=doc_id,
                )
                client.create_document(request=request)
                documents_imported += 1
            except Exception as e:
                # Try update if document already exists
                try:
                    document.name = f"{parent}/documents/{doc_id}"
                    request = discoveryengine.UpdateDocumentRequest(
                        document=document,
                        allow_missing=True,
                    )
                    client.update_document(request=request)
                    documents_imported += 1
                except Exception as update_err:
                    print(f"  Warning: Failed to import chunk {i} of {file_path}: {update_err}")

    if documents_imported == 0:
        print("Error: No documents were imported")
        return False

    print(f"Successfully imported {documents_imported} document chunks")
    return True


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Seed Vertex AI Search Datastore with Educational Content")
    parser.add_argument("--project", required=True, help="GCP Project ID")
    parser.add_argument("--location", default="global", help="Datastore location (default: global)")
    parser.add_argument("--datastore-id", required=True, help="Discovery Engine datastore ID")
    parser.add_argument("--data-dir", help="Path to directory containing educational content (.txt files)")
    parser.add_argument("--sample", action="store_true", help="Seed with built-in sample educational content")

    args = parser.parse_args()

    if not args.data_dir and not args.sample:
        parser.error("Please provide either --data-dir <path> or --sample to specify content source")

    if args.sample:
        success = seed_from_sample(args.project, args.location, args.datastore_id)
    else:
        success = seed_from_directory(args.project, args.location, args.datastore_id, args.data_dir)

    exit(0 if success else 1)
