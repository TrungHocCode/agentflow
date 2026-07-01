import sqlite3
from pydantic import BaseModel, Field
from langchain_core.tools import tool
from app.execution.tools.base import ToolRegistry

class DatabaseQueryInput(BaseModel):
    query: str = Field(description="The SQL query to execute against the database. Tables available: users(id, name, role, email), system_metrics(metric_name, value)")

@ToolRegistry.register_tool(name="database_query")
@tool("database_query", args_schema=DatabaseQueryInput)
def database_query(query: str) -> str:
    """
    Executes a SQL query against the database and returns the rows as a formatted string.
    Use this to retrieve data about users, workflows, settings, or products.
    """
    try:
        # Create an in-memory database and populate it with sample tables for sandbox runs
        conn = sqlite3.connect(":memory:")
        cursor = conn.cursor()
        
        # Create schema
        cursor.execute("CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT, role TEXT, email TEXT)")
        cursor.execute("INSERT INTO users VALUES (1, 'Alice', 'admin', 'alice@platform.com')")
        cursor.execute("INSERT INTO users VALUES (2, 'Bob', 'worker', 'bob@platform.com')")
        cursor.execute("INSERT INTO users VALUES (3, 'Charlie', 'user', 'charlie@platform.com')")
        
        cursor.execute("CREATE TABLE system_metrics (metric_name TEXT, value REAL)")
        cursor.execute("INSERT INTO system_metrics VALUES ('cpu_usage', 24.5)")
        cursor.execute("INSERT INTO system_metrics VALUES ('memory_usage', 56.2)")
        
        conn.commit()
        
        # Run query
        cursor.execute(query)
        rows = cursor.fetchall()
        
        if not rows:
            return "Query executed successfully. No rows returned."
            
        # Format output
        headers = [desc[0] for desc in cursor.description]
        header_str = " | ".join(headers)
        separator = "-" * len(header_str)
        
        row_strings = []
        for row in rows:
            row_strings.append(" | ".join(str(val) for val in row))
            
        conn.close()
        return f"{header_str}\n{separator}\n" + "\n".join(row_strings)
        
    except Exception as e:
        return f"Error executing database query: {str(e)}"
