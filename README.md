# 🤖 AI Data Agent

A multi-agent system that intelligently processes data queries and automates data operations. Built with LangGraph, this project showcases how specialized agents collaborate to handle SQL database queries and ETL (Extract-Transform-Load) workflows through natural language commands.

## YouTube Tutorial
https://youtu.be/7yOmi4IX-Rs?si=_NGAHOomEPocRoqt

## 📋 Table of Contents

- [Overview](#overview)
- [Architecture](#architecture)
- [Features](#features)
- [Prerequisites](#prerequisites)
- [Installation](#installation)
- [Project Structure](#project-structure)
- [Configuration](#configuration)
- [Usage](#usage)
- [Agent Descriptions](#agent-descriptions)
- [Data Models](#data-models)
- [Examples](#examples)
- [Contributing](#contributing)

---

## 🎯 Overview

The **AI Data Agent** understands natural language requests and intelligently routes them to the right processing engine. When you ask a question, the main router determines whether you need database access (SQL Analyst) or data transformation (ETL Analyst), then delegates the work accordingly.

Key capabilities include:
- **Smart request routing** — Automatically detects whether you need SQL queries or data transformations
- **Natural language processing** — Convert plain English into executable operations
- **Multi-agent coordination** — Specialized agents handle SQL and ETL separately
- **Safety checks** — Validates database queries before execution
- **Flexible LLM selection** — Uses faster models for simple tasks, premium models for complex ones

---

## 🏗️ Architecture

The system follows a hierarchical agent architecture:

```
┌─────────────────────────────────────────────────────────────┐
│                    Data Agent (Router)                      │
│         Routes user queries to appropriate sub-agents       │
└────────────────────┬────────────────────────────────────────┘
                     │
         ┌───────────┴───────────┐
         │                       │
         ▼                       ▼
    ┌──────────────┐        ┌──────────────┐
    │ SQL Analyst  │        │ ETL Analyst  │
    │   Agent      │        │   Agent      │
    └──────────────┘        └──────────────┘
         │                       │
         ├─► Query Curation      ├─► Extract Load
         ├─► Schema Context      ├─► Transform Load
         ├─► SQL Generation      └─► Code Execution
         ├─► Safety Validation   
         ├─► Query Execution     
         └─► Answer Generation   
```

### State Flow

1. **User Input** → Natural language query
2. **Router Node** → Classifies query as SQL or ETL
3. **Agent Dispatch** → Routes to appropriate sub-agent
4. **Processing** → Each agent processes the task
5. **Output** → Returns structured result to user

---

## ✨ Features

### SQL Agent
- Converts natural language questions into SQL queries
- Automatically fetches database schema for context
- Validates queries for safety before execution
- Protects against destructive operations (INSERT, UPDATE, DELETE, DROP, etc.)
- Formats and returns results clearly

### ETL Agent
- Extracts data from APIs and converts JSON responses to structured formats
- Transforms data using Pandas for filtering, aggregation, and restructuring
- Supports multiple output formats: CSV, JSON, and Parquet
- Generates and executes code safely in a controlled environment

### Intelligent Design
- **Request Classification** — Automatically identifies whether you need SQL queries or data transformations
- **Dynamic Model Selection** — Uses cost-effective models for simple queries, premium Claude for complex tasks
- **Built-in Safety** — All operations are validated before execution
- **Structured Data** — Uses Pydantic for reliable data validation across the system

---

## 📦 Prerequisites

- Python 3.12+
- PostgreSQL database (for SQL operations)
- API keys for LLM providers (Claude and/or OpenAI)
- Virtual environment (recommended)

---

## 🚀 Installation

### Step 1: Set up your environment

```bash
cd Data_Agent
python -m venv .venv

# Activate the virtual environment
# Windows:
.\.venv\Scripts\Activate.ps1
# macOS/Linux:
source .venv/bin/activate
```

### Step 2: Install dependencies

```bash
pip install -r requirements.txt
```

The project requires:
- **langchain** — LLM framework
- **langgraph** — Multi-agent orchestration
- **langchain-anthropic** — Claude integration
- **langchain-openai** — OpenAI integration
- **pandas** — Data manipulation
- **psycopg2** — PostgreSQL driver
- **pydantic** — Data validation
- **python-dotenv** — Configuration management

### Step 3: Configure your credentials

Create a `.env` file in the root directory with your API keys and database settings:

```env
# API Keys
ANTHROPIC_API_KEY=your_claude_key
OPENAI_API_KEY=your_openai_key

# PostgreSQL Database
host=localhost
port=5432
user=postgres
password=your_password
database=data_agent_db

# Optional: Custom Model Selection
LLM_MODEL_LOW=gpt-3.5-turbo
LLM_MODEL_MEDIUM=gpt-4-turbo
LLM_MODEL_HIGH=claude-3-opus
```

---

## 📁 Project Structure

```
Data_Agent/
├── agents/                          # Agent implementations
│   ├── __init__.py
│   ├── data_agent.py               # Main router agent
│   ├── sql_analyst.py              # SQL query agent
│   └── etl_analyst.py              # ETL operations agent
│
├── Models/                          # Data models
│   ├── __init__.py
│   └── schema.py                   # Pydantic schemas for state management
│
├── utils/                           # Utility modules
│   ├── __init__.py
│   ├── database.py                 # PostgreSQL utilities
│   ├── etl_tools.py                # ETL operations toolkit
│   ├── llm_pick.py                 # LLM selection logic
│
├── data/                            # Data directory
│   ├── extract/                     # Extracted data storage
│   ├── transform/                   # Transformed data storage
│   ├── payments.csv                 # Sample dataset
│   ├── ratings.csv                  # Sample dataset
│   ├── rides.csv                    # Sample dataset
│   ├── users.csv                    # Sample dataset
│   └── vehicles.csv                 # Sample dataset
│
├── main.py                          # Entry point
├── feed_db.py                       # Database initialization script
├── pyproject.toml                   # Project metadata and dependencies
└── README.md                         # This file
```

---

## ⚙️ Configuration

The system adapts its behavior based on task complexity:

**LLM Selection** (`utils/llm_pick.py`)
- Simple queries use fast, cost-effective models
- Moderate complexity uses balanced models
- Complex tasks use Claude for maximum capability

**Database Connection** (`utils/database.py`)
- Reads PostgreSQL credentials from `.env`
- Automatically fetches schema information
- Manages connection pooling and error handling

---

## 💻 Usage

### As a Python Module

```python
from agents.data_agent import data_agent
from langchain_core.messages import HumanMessage

response = data_agent.invoke({
    "messages": [
        HumanMessage(content="Show me the top 5 users by rating")
    ],
    "route_response": ""
})

print(response)
```

### From the Command Line

```bash
python main.py          # Run the interactive agent
python agents/sql_analyst.py   # Run just the SQL agent
python agents/etl_analyst.py   # Run just the ETL agent
```

Just ask natural language questions — the system figures out whether you need SQL or ETL and handles it accordingly.

---

## 🤖 Agent Descriptions

### Data Agent (Router)
**File:** `agents/data_agent.py`

The main entry point that understands your intent and directs the request to the right specialist. It analyzes whether you're asking for a database query or a data transformation, then invokes the appropriate agent.

### SQL Analyst Agent
**File:** `agents/sql_analyst.py`

Handles all database questions by converting them into SQL. The workflow is:
1. Understands your question clearly
2. Fetches the database schema for context
3. Generates a SQL query
4. Validates the query for safety (no DELETE, DROP, etc.)
5. Executes the query and returns results

**Protection:** Blocks destructive operations and limits results to 10 rows by default.

### ETL Analyst Agent
**File:** `agents/etl_analyst.py`

Handles data extraction and transformation tasks. It can:
- Extract data from APIs and normalize JSON responses
- Transform existing CSV/JSON files using Pandas
- Save results in CSV, JSON, or Parquet format

All code generation and execution happens in a controlled, safe environment.

---

## 📊 Data Models

The system uses Pydantic models for type-safe state management:

- **AgentSchema** — Tracks SQL processing (questions, generated queries, safety checks, results)
- **ETLAgentSchema** — Manages conversation history for ETL operations
- **RouterSchema** — Classifies requests as SQL or ETL with reasoning
- **DataAgentSchema** — Top-level state holding messages and routing decisions

See `Models/schema.py` for complete definitions.

---

## 📚 Examples

**SQL Query — Database Analysis**
```
"Show me the average rating for each vehicle type"
```
Result: Router → SQL Agent → Schema lookup → SQL generation → Safety check → Database query

**API Data Extraction**
```
"Extract Pokémon data from https://pokeapi.co/api/v2/pokemon and save as CSV"
```
Result: Router → ETL Agent → API request → Normalize JSON → Save to data/extract/

**Data Transformation**
```
"Filter rides.csv to show only ratings above 4.0 and save as JSON"
```
Result: Router → ETL Agent → Generate Pandas code → Execute safely → Save to data/transform/

---

## 🔐 Security

- **SQL Safety** — All queries are analyzed before execution; destructive operations (INSERT, UPDATE, DELETE, DROP, ALTER) are blocked
- **Safe Code Execution** — Generated Python code runs in a controlled sandbox
- **Credential Management** — API keys and database passwords are read from `.env`, never hardcoded
- **Input Validation** — All inputs are validated using Pydantic schemas

---

## 🛠️ Extending the System

**Add a New Agent:**
1. Create a file in `agents/`
2. Define its state schema in `Models/schema.py`
3. Implement the agent logic using LangGraph
4. Update the router in `data_agent.py`

**Add ETL Tools:**
Edit `utils/etl_tools.py` and add tools using the `@tool` decorator for the agent to discover.

**Customize Model Selection:**
Modify `utils/llm_pick.py` to change which models are used for different complexity levels.

---

## 🚨 Common Issues

| Problem | Solution |
|---------|----------|
| Database connection fails | Verify PostgreSQL is running and `.env` credentials are correct |
| API key not found | Ensure `ANTHROPIC_API_KEY` and `OPENAI_API_KEY` are in `.env` |
| Query rejected as unsafe | Rewrite as SELECT-only; the system blocks INSERT, UPDATE, DELETE, DROP |
| Module not found | Activate the virtual environment: `.venv\Scripts\Activate.ps1` (Windows) or `source .venv/bin/activate` (macOS/Linux) |

---

## 📈 Tips for Better Performance

- Simple queries automatically use faster models to reduce latency and cost
- Add database indexes on frequently queried columns
- Respect API rate limits when extracting from external sources
- For large transformations, consider breaking them into smaller steps

---

## 🤝 Contributing

We welcome contributions! When adding features:
- Follow the existing code style and naming conventions
- Define state schemas in `Models/schema.py` for new agents
- Consider security implications (especially for SQL and code execution)
- Add documentation for new features

---

## 📚 Learning Resources

To understand the technologies used:
- [LangGraph](https://langchain-ai.github.io/langgraph/) — Multi-agent orchestration
- [LangChain](https://python.langchain.com/) — LLM framework
- [Claude API](https://docs.anthropic.com/) — Anthropic's models
- [PostgreSQL](https://www.postgresql.org/docs/) — Database system

---

## 📄 License

This project is part of an AI engineering demonstration.

---

**Version:** 0.1.0 | **Last Updated:** October 2026