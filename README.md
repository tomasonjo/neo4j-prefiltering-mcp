# Neo4j Prefiltering Vector Search MCP Server

An MCP (Model Context Protocol) server that automatically discovers vector indexes in a Neo4j database and exposes each one as a semantic search tool. Built with [FastMCP](https://github.com/jlowin/fastmcp) and [LangChain](https://python.langchain.com/) embeddings, so it works with any embedding provider out of the box.

## How It Works

On startup the server:

1. Connects to Neo4j and runs `SHOW INDEXES` to find every `VECTOR` index.
2. Samples one node per indexed property to detect its type (string, numeric, date, bool, or vector).
3. Identifies the embedding property and the remaining filterable metadata properties.
4. Registers an MCP tool `search_<index_name>` for each discovered index, complete with a dynamically generated description listing the available filters.

If no vector indexes are found, the server exits with an error.

## Prerequisites

- Python 3.10+
- A running Neo4j instance (5.x+ with vector index support)
- At least one vector index already created in the database
- An API key or credentials for your chosen embedding provider

## Installation

First, clone the repository:

```bash
git clone https://github.com/tomasonjo/neo4j-prefiltering-mcp.git
cd neo4j-prefiltering-mcp
```

### Using uvx (recommended)

No installation needed — just run it directly from the local folder:

```bash
uvx --from /path/to/neo4j-prefiltering-mcp neo4j-prefiltering-mcp
```

### Using pip

```bash
pip install /path/to/neo4j-prefiltering-mcp
```

Then run:

```bash
neo4j-prefiltering-mcp
```

### Embedding providers

The base package does not include an embedding provider. Install the one you need as an extra:

```bash
# OpenAI
pip install "/path/to/neo4j-prefiltering-mcp[openai]"

# Cohere
pip install "/path/to/neo4j-prefiltering-mcp[cohere]"

# HuggingFace
pip install "/path/to/neo4j-prefiltering-mcp[huggingface]"
```

Or with uvx:

```bash
uvx --from /path/to/neo4j-prefiltering-mcp --with langchain-openai neo4j-prefiltering-mcp
```

## Configuration

All configuration is done through environment variables.

| Variable | Default | Description |
|---|---|---|
| `NEO4J_URI` | `bolt://localhost:7687` | Neo4j connection URI |
| `NEO4J_USER` | `neo4j` | Neo4j username |
| `NEO4J_PASSWORD` | `password` | Neo4j password |
| `NEO4J_DATABASE` | `neo4j` | Neo4j database name |
| `EMBEDDING_MODEL` | `openai:text-embedding-3-small` | LangChain embedding model spec |

The `EMBEDDING_MODEL` value is passed directly to `langchain.embeddings.init_embeddings()`. Any provider string it supports will work:

```bash
# OpenAI
export EMBEDDING_MODEL="openai:text-embedding-3-small"

# Cohere
export EMBEDDING_MODEL="cohere:embed-english-v3.0"

# HuggingFace
export EMBEDDING_MODEL="huggingface:BAAI/bge-small-en-v1.5"
```

Make sure the corresponding provider SDK and API key env var are set (e.g. `OPENAI_API_KEY`, `COHERE_API_KEY`).

## Usage

### Claude Desktop

Add the server to your `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "neo4j-vector": {
      "command": "uvx",
      "args": ["--from", "/path/to/neo4j-prefiltering-mcp", "--with", "langchain-openai", "neo4j-prefiltering-mcp"],
      "env": {
        "NEO4J_URI": "bolt://localhost:7687",
        "NEO4J_USER": "neo4j",
        "NEO4J_PASSWORD": "your-password",
        "NEO4J_DATABASE": "neo4j",
        "EMBEDDING_MODEL": "openai:text-embedding-3-small",
        "OPENAI_API_KEY": "sk-..."
      }
    }
  }
}
```

### Claude Code

```bash
claude mcp add neo4j-vector -- uvx --from /path/to/neo4j-prefiltering-mcp --with langchain-openai neo4j-prefiltering-mcp
```

### Standalone

```bash
neo4j-prefiltering-mcp
```

The server communicates over stdio by default, which is the standard transport for local MCP tool servers.

### Cursor / Continue / Other MCP Clients

Point the client at the server as a stdio server. The exact config format varies by client — consult its docs and use the command + args pattern shown above.

## Tool Interface

Each discovered index is exposed as a tool with the following parameters:

| Parameter | Type | Required | Description |
|---|---|---|---|
| `query` | `str` | Yes | Natural-language search text (embedded at call time) |
| `top_k` | `int` | No | Number of results to return (default `10`) |
| `filters` | `dict` | No | Metadata filters (keys and accepted types are index-specific) |

### Filter Types

The server infers filter types by sampling a node from each index:

| Detected Type | Filter Format | Example |
|---|---|---|
| `float` / `int` | `{"min": ..., "max": ...}` | `{"min": 0.5, "max": 1.0}` |
| `date` | `{"min": "...", "max": "..."}` | `{"min": "2024-01-01", "max": "2024-12-31"}` |
| `bool` | `true` / `false` | `true` |
| `string` | `"exact value"` | `"en"` |

Both `min` and `max` are optional within a range filter — you can supply either or both.

### Example Tool Call

Given an index called `news_articles` on `:Article` nodes with metadata properties `language` (string) and `sentiment` (float):

```json
{
  "name": "search_news_articles",
  "arguments": {
    "query": "recent breakthroughs in fusion energy",
    "top_k": 5,
    "filters": {
      "language": "en",
      "sentiment": { "min": 0.6 }
    }
  }
}
```

### Response Format

The tool returns a JSON array of results, each containing the matched node's properties (minus the raw embedding vector) and a similarity score:

```json
[
  {
    "doc": {
      "title": "Fusion Milestone Reached at NIF",
      "language": "en",
      "sentiment": 0.92,
      "published": "2025-01-15"
    },
    "score": 0.941
  }
]
```

## Project Structure

```
.
├── pyproject.toml
├── src/
│   └── neo4j_prefiltering_mcp/
│       ├── __init__.py
│       └── server.py
└── README.md
```

## License

MIT
