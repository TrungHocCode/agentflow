from pydantic import BaseModel, Field
from langchain_core.tools import tool
from app.execution.tools.base import ToolRegistry

class EmailSenderInput(BaseModel):
    recipient: str = Field(description="The recipient email address (e.g. 'client@example.com').")
    subject: str = Field(description="The email subject line.")
    body: str = Field(description="The body content of the email.")

@ToolRegistry.register_tool(name="email_sender")
@tool("email_sender", args_schema=EmailSenderInput)
def email_sender(recipient: str, subject: str, body: str) -> str:
    """
    Sends an email to the specified recipient.
    In the testing sandbox, it simulates transmission and outputs the email envelope.
    """
    del body
    return f"Successfully sent email to '{recipient}' (Subject: '{subject}'). [Simulated]"
