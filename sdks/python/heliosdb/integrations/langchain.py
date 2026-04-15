"""
LangChain integration for HeliosDB.

Provides LangChain-compatible VectorStore, Memory, and Retriever implementations
for building RAG pipelines and AI agents backed by HeliosDB.

Classes:
    HeliosDBVectorStore: LangChain VectorStore for similarity search.
    HeliosDBRetriever: LangChain BaseRetriever wrapping a HeliosDB vector store.
    HeliosDBChatMemory: LangChain BaseMemory for conversation persistence.
    HeliosDBDocumentLoader: LangChain BaseLoader for SQL-based document loading.
"""

from __future__ import annotations

from typing import Any, Iterable, List, Optional, Tuple, Type

try:
    from langchain_core.documents import Document
    from langchain_core.embeddings import Embeddings
    from langchain_core.vectorstores import VectorStore
    from langchain_core.memory import BaseMemory
    from langchain_core.document_loaders import BaseLoader
    from langchain_core.retrievers import BaseRetriever
    from langchain_core.callbacks import CallbackManagerForRetrieverRun

    LANGCHAIN_AVAILABLE = True
except ImportError:
    LANGCHAIN_AVAILABLE = False
    # Create placeholder classes for type hints
    Document = Any  # type: ignore
    Embeddings = Any  # type: ignore
    VectorStore = object  # type: ignore
    BaseMemory = object  # type: ignore
    BaseLoader = object  # type: ignore
    BaseRetriever = object  # type: ignore
    CallbackManagerForRetrieverRun = Any  # type: ignore

from heliosdb.client import HeliosDB


def _check_langchain() -> None:
    """Check if LangChain is available."""
    if not LANGCHAIN_AVAILABLE:
        raise ImportError(
            "langchain-core is required for LangChain integration. "
            "Install with: pip install heliosdb[langchain]"
        )


class HeliosDBVectorStore(VectorStore if LANGCHAIN_AVAILABLE else object):  # type: ignore
    """LangChain VectorStore backed by HeliosDB vector search.

    Wraps a HeliosDB vector collection as a LangChain-compatible
    ``VectorStore``, supporting both local and server-side embeddings,
    metadata filtering, and HNSW-accelerated similarity search.

    Args:
        connection_string: HeliosDB server URL (``http://…``) or local file path.
        collection_name: Name of the vector collection to use or create.
        embedding: A LangChain ``Embeddings`` instance for client-side embedding.
            When *None*, the server generates embeddings automatically.
        api_key: Optional API key for authenticated connections.
        dimension: Dimensionality of the embedding vectors (default 1536 for
            OpenAI ``text-embedding-ada-002``).
        metric: Distance metric — one of ``"cosine"``, ``"euclidean"``, or
            ``"dot_product"``.

    Example::

        from langchain_openai import OpenAIEmbeddings
        from heliosdb.integrations.langchain import HeliosDBVectorStore

        embeddings = OpenAIEmbeddings()
        vectorstore = HeliosDBVectorStore(
            connection_string="http://localhost:8080",
            collection_name="documents",
            embedding=embeddings,
        )

        # Add documents
        vectorstore.add_documents(docs)

        # Similarity search
        results = vectorstore.similarity_search("query", k=5)

        # Search with scores
        scored = vectorstore.similarity_search_with_score("query", k=3)
        for doc, score in scored:
            print(f"{score:.3f}  {doc.page_content[:80]}")

        # Build from texts in one step
        vs = HeliosDBVectorStore.from_texts(
            texts=["Hello world", "HeliosDB rocks"],
            embedding=embeddings,
            connection_string="http://localhost:8080",
        )
    """

    def __init__(
        self,
        connection_string: str = "http://localhost:8080",
        collection_name: str = "documents",
        embedding: Optional[Embeddings] = None,
        api_key: Optional[str] = None,
        dimension: int = 1536,
        metric: str = "cosine",
    ) -> None:
        """Initialize HeliosDB VectorStore.

        Args:
            connection_string: HeliosDB server URL or file path.
            collection_name: Name of the vector collection.
            embedding: LangChain Embeddings instance for client-side embedding.
            api_key: API key for authentication.
            dimension: Vector dimension (must match the embedding model output).
            metric: Distance metric (``cosine``, ``euclidean``, ``dot_product``).
        """
        _check_langchain()

        self._client = HeliosDB.connect(connection_string, api_key=api_key)
        self._collection_name = collection_name
        self._embedding = embedding
        self._dimension = dimension
        self._metric = metric
        self._store = self._client.vector_store(
            collection_name,
            dimension=dimension,
            metric=metric,
        )

    @property
    def embeddings(self) -> Optional[Embeddings]:
        """Return the embeddings instance."""
        return self._embedding

    def add_texts(
        self,
        texts: Iterable[str],
        metadatas: Optional[List[dict]] = None,
        **kwargs: Any,
    ) -> List[str]:
        """Add raw text strings to the vector store.

        Each text is embedded (client-side if an ``Embeddings`` instance was
        provided, otherwise server-side) and stored alongside its metadata.

        Args:
            texts: Iterable of text strings to embed and store.
            metadatas: Optional list of metadata dicts, one per text.
                Length must match the number of texts when provided.
            **kwargs: Additional keyword arguments.  Pass ``ids`` as a list
                of string IDs to use instead of auto-generated UUIDs.

        Returns:
            List[str]: Ordered list of IDs assigned to the stored vectors.

        Example::

            ids = vectorstore.add_texts(
                texts=["first doc", "second doc"],
                metadatas=[{"source": "web"}, {"source": "pdf"}],
            )
        """
        texts_list = list(texts)

        if self._embedding is None:
            # Use server-side embedding
            return self._store.add_texts(
                texts_list,
                metadatas=metadatas,
            )

        # Embed locally and upsert
        embeddings = self._embedding.embed_documents(texts_list)
        vectors = []
        for i, (text, embedding) in enumerate(zip(texts_list, embeddings)):
            vector_id = kwargs.get("ids", [None] * len(texts_list))[i]
            if vector_id is None:
                import uuid
                vector_id = str(uuid.uuid4())

            metadata = metadatas[i] if metadatas and i < len(metadatas) else {}
            metadata["text"] = text

            vectors.append({
                "id": vector_id,
                "vector": embedding,
                "metadata": metadata,
            })

        self._store.upsert(vectors)
        return [v["id"] for v in vectors]

    def add_documents(
        self,
        documents: List[Document],
        **kwargs: Any,
    ) -> List[str]:
        """Add LangChain ``Document`` objects to the vector store.

        Convenience wrapper around :meth:`add_texts` that extracts
        ``page_content`` and ``metadata`` from each document automatically.

        Args:
            documents: List of LangChain ``Document`` instances to add.
            **kwargs: Forwarded to :meth:`add_texts` (e.g. ``ids``).

        Returns:
            List[str]: Ordered list of IDs assigned to the stored vectors.

        Example::

            from langchain_core.documents import Document

            docs = [
                Document(page_content="HeliosDB supports HNSW", metadata={"ch": 1}),
                Document(page_content="Branch isolation is built-in", metadata={"ch": 2}),
            ]
            ids = vectorstore.add_documents(docs)
        """
        texts = [doc.page_content for doc in documents]
        metadatas = [doc.metadata for doc in documents]
        return self.add_texts(texts, metadatas, **kwargs)

    def similarity_search(
        self,
        query: str,
        k: int = 4,
        **kwargs: Any,
    ) -> List[Document]:
        """Return the *k* most similar documents to a text query.

        The query is embedded using the configured ``Embeddings`` instance and
        then matched against stored vectors via HNSW approximate nearest
        neighbour search.

        Args:
            query: Natural-language query string.
            k: Number of top results to return (default 4).
            **kwargs: Optional ``filter`` dict for metadata filtering.

        Returns:
            List[Document]: Documents ordered by descending similarity.
                Each document's ``metadata`` includes ``score`` (float) and
                ``id`` (str) fields injected by the search.

        Raises:
            ValueError: If no ``Embeddings`` instance was provided at init.

        Example::

            results = vectorstore.similarity_search("vector databases", k=3)
            for doc in results:
                print(doc.page_content, doc.metadata["score"])
        """
        if self._embedding is None:
            raise ValueError("Embeddings are required for similarity_search")

        query_embedding = self._embedding.embed_query(query)
        return self.similarity_search_by_vector(query_embedding, k, **kwargs)

    def similarity_search_by_vector(
        self,
        embedding: List[float],
        k: int = 4,
        **kwargs: Any,
    ) -> List[Document]:
        """Return the *k* most similar documents to a pre-computed vector.

        Use this instead of :meth:`similarity_search` when you already have an
        embedding vector and want to skip the embedding step.

        Args:
            embedding: Query vector (list of floats matching the store dimension).
            k: Number of top results to return (default 4).
            **kwargs: Optional ``filter`` dict for metadata filtering.

        Returns:
            List[Document]: Documents ordered by descending similarity.
        """
        filter_dict = kwargs.get("filter")
        results = self._store.search(
            embedding,
            top_k=k,
            filter=filter_dict,
            include_metadata=True,
        )

        documents = []
        for result in results:
            text = result.metadata.pop("text", "")
            documents.append(Document(
                page_content=text,
                metadata={
                    **result.metadata,
                    "score": result.score,
                    "id": result.id,
                },
            ))
        return documents

    def similarity_search_with_score(
        self,
        query: str,
        k: int = 4,
        **kwargs: Any,
    ) -> List[Tuple[Document, float]]:
        """Return the *k* most similar documents together with their scores.

        This is the preferred method when you need to inspect or threshold on
        relevance scores (e.g. to discard low-confidence results in a RAG
        pipeline).

        Args:
            query: Natural-language query string.
            k: Number of top results to return (default 4).
            **kwargs: Optional ``filter`` dict for metadata filtering.

        Returns:
            List[Tuple[Document, float]]: List of ``(document, score)`` tuples
                ordered by descending similarity.  The score semantics depend
                on the configured distance metric.

        Example::

            scored = vectorstore.similarity_search_with_score("HeliosDB features", k=5)
            for doc, score in scored:
                if score > 0.8:
                    print(f"[{score:.2f}] {doc.page_content[:60]}")
        """
        docs = self.similarity_search(query, k, **kwargs)
        return [(doc, doc.metadata.get("score", 0.0)) for doc in docs]

    @classmethod
    def from_texts(
        cls: Type["HeliosDBVectorStore"],
        texts: List[str],
        embedding: Embeddings,
        metadatas: Optional[List[dict]] = None,
        **kwargs: Any,
    ) -> "HeliosDBVectorStore":
        """Create a new vector store and populate it from plain texts.

        This is a convenience factory that instantiates the store, embeds the
        provided texts, and returns the ready-to-query instance.

        Args:
            texts: List of text strings to embed and store.
            embedding: A LangChain ``Embeddings`` instance.
            metadatas: Optional list of metadata dicts (one per text).
            **kwargs: Forwarded to the ``HeliosDBVectorStore`` constructor.
                Commonly used keys: ``connection_string``, ``collection_name``,
                ``dimension``, ``metric``, ``api_key``.

        Returns:
            HeliosDBVectorStore: A new vector store pre-loaded with the texts.

        Example::

            vs = HeliosDBVectorStore.from_texts(
                texts=["doc one", "doc two"],
                embedding=OpenAIEmbeddings(),
                connection_string="http://localhost:8080",
                collection_name="my_docs",
            )
            results = vs.similarity_search("query")
        """
        connection_string = kwargs.pop("connection_string", "http://localhost:8080")
        collection_name = kwargs.pop("collection_name", "documents")

        store = cls(
            connection_string=connection_string,
            collection_name=collection_name,
            embedding=embedding,
            **kwargs,
        )
        store.add_texts(texts, metadatas)
        return store

    @classmethod
    def from_documents(
        cls: Type["HeliosDBVectorStore"],
        documents: List[Document],
        embedding: Embeddings,
        **kwargs: Any,
    ) -> "HeliosDBVectorStore":
        """Create a new vector store and populate it from LangChain Documents.

        Equivalent to calling :meth:`from_texts` after extracting each
        document's ``page_content`` and ``metadata``.

        Args:
            documents: List of LangChain ``Document`` instances.
            embedding: A LangChain ``Embeddings`` instance.
            **kwargs: Forwarded to the ``HeliosDBVectorStore`` constructor
                (see :meth:`from_texts` for accepted keys).

        Returns:
            HeliosDBVectorStore: A new vector store pre-loaded with the documents.

        Example::

            from langchain_core.documents import Document

            docs = loader.load()  # any LangChain loader
            vs = HeliosDBVectorStore.from_documents(docs, OpenAIEmbeddings())
        """
        texts = [doc.page_content for doc in documents]
        metadatas = [doc.metadata for doc in documents]
        return cls.from_texts(texts, embedding, metadatas, **kwargs)

    def delete(self, ids: Optional[List[str]] = None, **kwargs: Any) -> Optional[bool]:
        """Delete vectors from the store by their IDs.

        Args:
            ids: List of vector IDs to remove.  If *None* or empty, no
                action is taken and ``False`` is returned.
            **kwargs: Reserved for future use.

        Returns:
            Optional[bool]: ``True`` if vectors were deleted, ``False`` otherwise.
        """
        if ids:
            self._store.delete(ids)
            return True
        return False


class HeliosDBRetriever(BaseRetriever if LANGCHAIN_AVAILABLE else object):  # type: ignore
    """LangChain retriever that queries a HeliosDB vector store.

    Implements the LangChain ``BaseRetriever`` interface so it can be plugged
    directly into any LangChain chain or agent that expects a retriever
    (e.g. ``RetrievalQA``, ``ConversationalRetrievalChain``).

    The retriever delegates to :class:`HeliosDBVectorStore.similarity_search`
    under the hood, so it supports the same HNSW-accelerated search and
    metadata filtering.

    Args:
        client: A connected :class:`~heliosdb.client.HeliosDB` instance.
        collection: Name of the vector collection to search.
        k: Number of documents to retrieve per query (default 5).
        embedding: Optional LangChain ``Embeddings`` instance.  Required when
            the collection was populated with client-side embeddings.
        dimension: Vector dimension (default 1536).
        metric: Distance metric (default ``"cosine"``).

    Example::

        from heliosdb import HeliosDB
        from heliosdb.integrations.langchain import HeliosDBRetriever

        db = HeliosDB.connect("http://localhost:8080")
        retriever = HeliosDBRetriever(
            client=db,
            collection="docs",
            k=5,
        )
        docs = retriever.get_relevant_documents("What is HeliosDB?")
        for doc in docs:
            print(doc.page_content)
    """

    # -- Pydantic / BaseRetriever fields --
    client: Any = None
    collection: str = "documents"
    k: int = 5
    embedding: Any = None
    dimension: int = 1536
    metric: str = "cosine"

    class Config:
        arbitrary_types_allowed = True

    def _get_relevant_documents(
        self,
        query: str,
        *,
        run_manager: Optional[CallbackManagerForRetrieverRun] = None,
    ) -> List[Document]:
        """Retrieve documents relevant to *query* from HeliosDB.

        This method is called internally by ``get_relevant_documents()``
        and ``ainvoke()``.  You should not need to call it directly.

        Args:
            query: The natural-language query string.
            run_manager: Optional callback manager (used by LangChain
                tracing / logging infrastructure).

        Returns:
            List[Document]: The *k* most relevant documents.
        """
        _check_langchain()

        store = HeliosDBVectorStore(
            connection_string=self.client._url if hasattr(self.client, "_url") else "http://localhost:8080",
            collection_name=self.collection,
            embedding=self.embedding,
            dimension=self.dimension,
            metric=self.metric,
        )

        # When no local embedding is configured, fall back to the raw
        # vector_store search API so server-side embedding is used.
        if self.embedding is None:
            vs = self.client.vector_store(self.collection)
            results = vs.search(query, top_k=self.k)
            return [
                Document(
                    page_content=r.get("content", r.get("text", "")),
                    metadata={k: v for k, v in r.items() if k not in ("content", "text")},
                )
                for r in (results if isinstance(results, list) else [])
            ]

        return store.similarity_search(query, k=self.k)


class HeliosDBChatMemory(BaseMemory if LANGCHAIN_AVAILABLE else object):  # type: ignore
    """LangChain ``BaseMemory`` backed by HeliosDB agent memory.

    Persists conversation history (human + AI messages) in a HeliosDB
    session so that it survives process restarts and can be shared across
    services.

    Args:
        connection_string: HeliosDB server URL or file path.
        session_id: Unique identifier for this conversation session.
        api_key: Optional API key for authenticated connections.
        memory_key: The key under which the formatted history string is
            returned (default ``"history"``).
        human_prefix: Label prepended to user messages (default ``"Human"``).
        ai_prefix: Label prepended to assistant messages (default ``"AI"``).

    Example::

        from heliosdb.integrations.langchain import HeliosDBChatMemory

        memory = HeliosDBChatMemory(
            connection_string="http://localhost:8080",
            session_id="user-123",
        )

        # Use with a LangChain agent or chain
        from langchain.agents import initialize_agent
        agent = initialize_agent(tools, llm, memory=memory)
    """

    def __init__(
        self,
        connection_string: str = "http://localhost:8080",
        session_id: str = "default",
        api_key: Optional[str] = None,
        memory_key: str = "history",
        human_prefix: str = "Human",
        ai_prefix: str = "AI",
    ) -> None:
        """Initialize HeliosDB Chat Memory.

        Args:
            connection_string: HeliosDB server URL.
            session_id: Unique session identifier.
            api_key: API key for authentication.
            memory_key: Key used to inject history into chain inputs.
            human_prefix: Prefix for human messages in the history string.
            ai_prefix: Prefix for AI messages in the history string.
        """
        _check_langchain()

        self._client = HeliosDB.connect(connection_string, api_key=api_key)
        self._memory = self._client.agent_memory(session_id)
        self.memory_key = memory_key
        self.human_prefix = human_prefix
        self.ai_prefix = ai_prefix

    @property
    def memory_variables(self) -> List[str]:
        """Return the list of keys this memory injects into chain inputs."""
        return [self.memory_key]

    def load_memory_variables(self, inputs: dict[str, Any]) -> dict[str, Any]:
        """Load conversation history and return it as a dict.

        Args:
            inputs: Current chain inputs (unused, but required by the
                ``BaseMemory`` interface).

        Returns:
            dict[str, Any]: Single-key dict mapping :attr:`memory_key` to a
                newline-delimited string of prefixed messages.
        """
        messages = self._memory.get_messages()
        history_lines = []
        for msg in messages:
            if msg.role == "user":
                history_lines.append(f"{self.human_prefix}: {msg.content}")
            elif msg.role == "assistant":
                history_lines.append(f"{self.ai_prefix}: {msg.content}")
            else:
                history_lines.append(f"{msg.role.title()}: {msg.content}")

        return {self.memory_key: "\n".join(history_lines)}

    def save_context(self, inputs: dict[str, Any], outputs: dict[str, str]) -> None:
        """Persist the input/output pair from a chain run to HeliosDB.

        Args:
            inputs: The chain inputs (typically contains an ``"input"`` key).
            outputs: The chain outputs (typically contains an ``"output"`` key).
        """
        self._memory.save_context(inputs, outputs)

    def clear(self) -> None:
        """Delete all messages in this session from HeliosDB."""
        self._memory.clear()


class HeliosDBDocumentLoader(BaseLoader if LANGCHAIN_AVAILABLE else object):  # type: ignore
    """LangChain document loader that reads from HeliosDB via SQL.

    Executes an arbitrary SQL query against a HeliosDB branch and converts
    each result row into a LangChain ``Document``.  One column is mapped to
    ``page_content`` while the remaining selected columns become metadata.

    Args:
        connection_string: HeliosDB server URL or file path.
        query: SQL ``SELECT`` query that returns the desired rows.
        content_column: Name of the column whose value becomes
            ``Document.page_content`` (default ``"content"``).
        metadata_columns: List of column names to include in
            ``Document.metadata``.  Columns not present in the result set
            are silently skipped.
        api_key: Optional API key for authenticated connections.
        branch: HeliosDB branch to query (default ``"main"``).

    Example::

        from heliosdb.integrations.langchain import HeliosDBDocumentLoader

        loader = HeliosDBDocumentLoader(
            connection_string="http://localhost:8080",
            query="SELECT id, content, metadata FROM documents",
            content_column="content",
            metadata_columns=["id", "metadata"],
        )
        docs = loader.load()
        print(f"Loaded {len(docs)} documents")
    """

    def __init__(
        self,
        connection_string: str = "http://localhost:8080",
        query: str = "",
        content_column: str = "content",
        metadata_columns: Optional[List[str]] = None,
        api_key: Optional[str] = None,
        branch: str = "main",
    ) -> None:
        """Initialize HeliosDB Document Loader.

        Args:
            connection_string: HeliosDB server URL.
            query: SQL query to load documents.
            content_column: Column containing document text.
            metadata_columns: Columns to include as metadata fields.
            api_key: API key for authentication.
            branch: HeliosDB branch to query against.
        """
        _check_langchain()

        self._client = HeliosDB.connect(connection_string, api_key=api_key)
        self._query = query
        self._content_column = content_column
        self._metadata_columns = metadata_columns or []
        self._branch = branch

    def load(self) -> List[Document]:
        """Execute the SQL query and return results as LangChain Documents.

        Returns:
            List[Document]: One document per result row.
        """
        result = self._client.query(self._query, branch=self._branch)
        documents = []

        for row_dict in result.to_dicts():
            content = row_dict.get(self._content_column, "")
            metadata = {
                col: row_dict.get(col)
                for col in self._metadata_columns
                if col in row_dict
            }
            documents.append(Document(page_content=str(content), metadata=metadata))

        return documents
