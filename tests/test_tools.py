import os
import sys
import unittest
import shutil
from unittest.mock import patch, MagicMock

# Adjust path to import from backend
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "backend")))

# Importing registry triggers autodiscovery automatically
import app.execution.tools.registry
from app.execution.state import Task, add_messages, add_results, update_plan
from app.execution.tools.base import ToolRegistry


class TestToolsAndReducers(unittest.TestCase):
    def tearDown(self):
        # Clean up files in workspace_data if created
        data_dir = os.path.join(os.getcwd(), "workspace_data")
        if os.path.exists(data_dir):
            shutil.rmtree(data_dir)

    def test_state_reducers(self):
        """Test the state reducers perform list merging correctly."""
        # 1. Test add_messages keeps last 10
        left_msgs = [f"Msg {i}" for i in range(8)]
        right_msgs = ["New Msg 1", "New Msg 2", "New Msg 3"]
        merged_msgs = add_messages(left_msgs, right_msgs)
        self.assertEqual(len(merged_msgs), 10)
        self.assertEqual(merged_msgs[0], "Msg 1")
        self.assertEqual(merged_msgs[-1], "New Msg 3")

        # 2. Test add_results merges correctly
        results_left = [{"task_id": 1, "result": "Done"}]
        results_right = [{"task_id": 2, "result": "Running"}]
        self.assertEqual(len(add_results(results_left, results_right)), 2)

        # 3. Test update_plan merges tasks by ID
        t1 = Task(id=1, node="A", status="pending", error=None, description="Task A")
        t2 = Task(id=2, node="B", status="pending", error=None, description="Task B")
        t1_updated = Task(id=1, node="A", status="done", error=None, description="Task A Updated")
        
        plan = [t1, t2]
        updated_plan = update_plan(plan, [t1_updated])
        
        self.assertEqual(len(updated_plan), 2)
        self.assertEqual(updated_plan[0].status, "done")
        self.assertEqual(updated_plan[0].description, "Task A Updated")
        self.assertEqual(updated_plan[1].status, "pending")

    def test_tool_registry_autodiscovery(self):
        """Test ToolRegistry list and retrieval works via autodiscovery."""
        tools = ToolRegistry.list_tools()
        self.assertIn("python_executor", tools)
        self.assertIn("web_search", tools)
        self.assertIn("file_writer", tools)
        self.assertIn("file_reader", tools)
        self.assertIn("database_query", tools)
        self.assertIn("http_request", tools)
        self.assertIn("email_sender", tools)

        self.assertEqual(ToolRegistry.get_tool("python_executor").name, "python_executor")

    def test_python_executor(self):
        """Test PythonExecutor executes code correctly in subprocess."""
        executor = ToolRegistry.get_tool("python_executor")
        code = "print(2 + 3)"
        output = executor.invoke({"code": code})
        self.assertEqual(output.strip(), "5")

        code_error = "import sys; sys.exit(1)"
        output_error = executor.invoke({"code": code_error})
        self.assertTrue("Exit code 1" in output_error)

    @patch("requests.post")
    def test_web_search(self, mock_post):
        """Test WebSearch returns mock search results."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = """
        <div class="result__body">
            <a class="result__snippet" href="http://example.com/item1">Snippet 1 details</a>
            <a class="result__url" href="http://example.com/item1">example.com/item1</a>
        </div>
        """
        mock_post.return_value = mock_response

        search_tool = ToolRegistry.get_tool("web_search")
        output = search_tool.invoke({"query": "testing"})
        self.assertTrue("Snippet 1" in output)
        self.assertTrue("example.com/item1" in output)

    def test_database_query_tool(self):
        """Test database query tool executes SQL on the SQLite sandbox."""
        db_tool = ToolRegistry.get_tool("database_query")
        
        # Test query users table
        res = db_tool.invoke({"query": "SELECT name, role FROM users WHERE id = 1"})
        self.assertTrue("Alice" in res)
        self.assertTrue("admin" in res)

    @patch("requests.request")
    def test_http_request_tool(self, mock_request):
        """Test HTTP request tool executes and outputs clean response results."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"message": "Success"}
        mock_response.text = '{"message": "Success"}'
        mock_request.return_value = mock_response

        http_tool = ToolRegistry.get_tool("http_request")
        res = http_tool.invoke({
            "url": "https://api.example.com",
            "method": "POST",
            "data": '{"test": true}'
        })
        self.assertTrue("HTTP Status: 200" in res)
        self.assertTrue("Success" in res)

    def test_email_sender_tool(self):
        """Test mock email sender tool executes successfully."""
        email_tool = ToolRegistry.get_tool("email_sender")
        res = email_tool.invoke({
            "recipient": "bob@example.com",
            "subject": "Platform test",
            "body": "Hello world"
        })
        self.assertTrue("Successfully sent" in res)
        self.assertTrue("bob@example.com" in res)

    def test_file_writer_and_reader(self):
        """Test file writing and reading within workspace."""
        writer = ToolRegistry.get_tool("file_writer")
        reader = ToolRegistry.get_tool("file_reader")
        filename = "test_run.txt"
        content = "Testing AgentFlow File Tools."
        
        # Write file
        write_res = writer.invoke({"filename": filename, "content": content})
        self.assertTrue("Successfully wrote" in write_res)

        # Verify it was saved inside workspace_data
        self.assertTrue(os.path.exists(os.path.join(os.getcwd(), "workspace_data", filename)))

        # Read file
        read_res = reader.invoke({"filename": filename})
        self.assertEqual(read_res, content)
        
        # Read missing file
        missing_res = reader.invoke({"filename": "missing.txt"})
        self.assertTrue("Error:" in missing_res)


if __name__ == "__main__":
    unittest.main()
