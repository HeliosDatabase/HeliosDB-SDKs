"""RAG Quickstart with HeliosDB + LangChain

Demonstrates:
1. Creating a vector store
2. Adding documents
3. Similarity search
4. RAG pipeline with retriever
"""
from heliosdb import HeliosDB
from heliosdb.integrations.langchain import HeliosDBVectorStore, HeliosDBRetriever

# Connect
db = HeliosDB.connect(url="http://localhost:8080")

# Create vector store
vs = HeliosDBVectorStore(
    connection_string="http://localhost:8080",
    collection_name="docs",
)

# Add documents
vs.add_texts(
    texts=[
        "HeliosDB is a PostgreSQL-compatible embedded database",
        "It supports vector search with HNSW indexes",
        "Git-like branching allows safe schema testing",
    ],
    metadatas=[{"source": "docs"}, {"source": "docs"}, {"source": "docs"}],
)

# Search
print("Similarity search results:")
results = vs.similarity_search("What is HeliosDB?", k=2)
for doc in results:
    print(f"  {doc.page_content}")

# RAG with retriever
retriever = HeliosDBRetriever(client=db, collection="docs", k=3)
docs = retriever.get_relevant_documents("vector search capabilities")
print(f"\nRetrieved {len(docs)} documents for RAG")
for doc in docs:
    print(f"  {doc.page_content}")
