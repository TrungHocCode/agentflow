import asyncio
import os
import sys
import uuid
from langchain_core.messages import HumanMessage, AIMessage

# Ensure backend path is in sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "backend")))

from app.execution.state import State, Task
from app.execution.graph import build_execution_graph, get_graph_config


async def main():
    print("=" * 70)
    print("💬 AGENTFLOW — CONVERSATIONAL SUPERVISOR CHATBOT DEMO")
    print("=" * 70)
    print("Bạn có thể trò chuyện với SupervisorAgent để làm rõ yêu cầu.")
    print("Supervisor sẽ lắng nghe, tư vấn và lập kế hoạch DAG trước khi thực thi.")
    print("Gõ 'exit' hoặc 'quit' để thoát.\n")

    available_models = ["qwen3:8b", "llama3:8b", "gemma2:latest", "qwen3:0.6b"]
    print("Các mô hình Ollama có sẵn trên máy:")
    for idx, m in enumerate(available_models, 1):
        print(f"  {idx}. {m}")
    
    choice = input("\nChọn model (1-4, mặc định 1 - qwen3:8b): ").strip()
    model_name = "qwen3:8b"
    if choice == "2":
        model_name = "llama3:8b"
    elif choice == "3":
        model_name = "gemma2:latest"
    elif choice == "4":
        model_name = "qwen3:0.6b"

    print(f"\n🔄 Đã chọn Model: [{model_name}]. Bắt đầu phiên hội thoại!\n")

    compiled_graph = build_execution_graph()
    run_id = str(uuid.uuid4())
    config = get_graph_config(run_id)

    # Shared conversation state across turns
    current_state: State = {
        "messages": [],
        "plan": [],
        "current_task": None,
        "logs": [],
        "result_storage": [],
        "mode": "conversation",
        "metadata": {
            "use_llm": True,
            "model_name": model_name
        }
    }

    while True:
        user_input = input("\n👤 You: ").strip()
        if not user_input:
            continue
        if user_input.lower() in ("exit", "quit"):
            print("\n👋 Cảm ơn bạn đã trải nghiệm AgentFlow Chatbot!")
            break

        # Append human message to history
        current_state["messages"].append(HumanMessage(content=user_input))

        print("\n🤔 SupervisorAgent đang suy luận & lập kế hoạch...")
        current_state = await compiled_graph.ainvoke(current_state, config=config)

        # Retrieve last message from Supervisor
        messages = current_state.get("messages", [])
        if messages:
            last_msg = messages[-1]
            content = last_msg.content if hasattr(last_msg, "content") else str(last_msg)
            print(f"\n🤖 SupervisorAgent: {content}")

        # Display current plan if generated
        plan = current_state.get("plan") or []
        if plan:
            print("\n📋 Kế hoạch DAG hiện tại:")
            for task in plan:
                icon = "✅" if task.status == "done" else ("⏳" if task.status == "running" else "📌")
                print(f"   {icon} Task {task.id} [{task.node}]: {task.description} ({task.status})")

        # If execution results were produced in this turn
        results = current_state.get("result_storage") or []
        if results and all(t.status == "done" for t in plan):
            print("\n🎉 TAT CẢ TASKS ĐÃ THỰC THI HOÀN TẤT!")
            for res in results:
                print(f"\n--- [Kết quả Task {res.get('task_id')} - Node '{res.get('node')}'] ---")
                res_text = str(res.get('result', ''))
                preview = res_text[:300] + "..." if len(res_text) > 300 else res_text
                print(preview)

            report_file = os.path.join(os.getcwd(), "workspace_data", "reports", "intelligence_report.md")
            if os.path.exists(report_file):
                print(f"\n📄 Báo cáo Markdown đã được lưu tại: {report_file}")


if __name__ == "__main__":
    asyncio.run(main())
