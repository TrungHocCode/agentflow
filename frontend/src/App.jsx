import React, { useState, useEffect, useRef } from 'react';
import Header from './components/Header';
import Sidebar from './components/Sidebar';
import ChatStudio from './components/ChatStudio';
import FlowCanvas from './components/FlowCanvas';
import CatalogBrowser from './components/CatalogBrowser';
import ExecutionTracker from './components/ExecutionTracker';
import AuthScreen from './components/AuthScreen';

import { 
  getCatalogTools, 
  getCatalogAgents, 
  createConversation,
  sendConversationMessage,
  subscribeConversationEvents,
  createWorkflow,
  createWorkflowRun,
  approveRun, 
  getRunDetails,
  subscribeRunSSEStream,
  logout as logoutUser
} from './services/api';

const APPROVAL_KEYWORDS = ['ok', 'đồng ý', 'chạy đi', 'thực thi', 'bắt đầu', 'yes', 'approve', 'run', 'thực hiện đi', 'thực hiện', 'duyệt', 'tiến hành'];

export default function App() {
  const [activeTab, setActiveTab] = useState('studio');
  const [selectedModel, setSelectedModel] = useState('qwen3:8b');
  const [backendStatus, setBackendStatus] = useState(false);
  const [authUser, setAuthUser] = useState(() => {
    if (!localStorage.getItem('agentflow_access_token')) return null;
    try { return JSON.parse(localStorage.getItem('agentflow_user') || 'null'); } catch { return null; }
  });

  // Catalogs
  const [tools, setTools] = useState([]);
  const [agents, setAgents] = useState([]);

  // Conversational & Execution State
  const [messages, setMessages] = useState([]);
  const [conversationId, setConversationId] = useState(null);
  const [currentRun, setCurrentRun] = useState(null);
  const [activePlan, setActivePlan] = useState([]);
  const [executionLogs, setExecutionLogs] = useState([]);
  const [executionResults, setExecutionResults] = useState([]);
  const [isProcessing, setIsProcessing] = useState(false);
  const [isStreaming, setIsStreaming] = useState(false);
  const [executionDuration, setExecutionDuration] = useState(null);
  const [authMessage, setAuthMessage] = useState('');
  const conversationStreamRef = useRef(null);

  const appendExecutionLogs = (entries, createdAt = null) => {
    const values = Array.isArray(entries) ? entries : [entries];
    setExecutionLogs(prev => [
      ...prev,
      ...values.map(entry => (
        typeof entry === 'object'
          ? entry
          : { message: String(entry), created_at: createdAt }
      ))
    ]);
  };

  // Load catalogs on mount
  useEffect(() => {
    async function loadCatalogs() {
      try {
        const [toolsData, agentsData] = await Promise.all([
          getCatalogTools(),
          getCatalogAgents()
        ]);
        setTools(toolsData || []);
        setAgents(agentsData || []);
        setBackendStatus(true);
      } catch (err) {
        console.warn('Backend API connection warning:', err);
        setBackendStatus(false);
        // Fallback mock data if API server is not running yet
        setTools([
          { name: 'news_crawler', description: 'Crawls articles & news paragraphs from URLs.' },
          { name: 'text_summarizer', description: 'Extracts concise bullet points from text.' },
          { name: 'markdown_report_generator', description: 'Compiles formatted markdown reports to workspace_data/reports/.' },
          { name: 'python_executor', description: 'Executes Python code in a safe subprocess.' },
          { name: 'database_query', description: 'Queries SQLite database tables.' },
          { name: 'email_sender', description: 'Sends mock emails to recipients.' },
          { name: 'http_request', description: 'Sends HTTP GET/POST requests.' },
          { name: 'web_search', description: 'Searches the web for relevant context.' }
        ]);
        setAgents([
          { name: 'news_crawler', tool_names: ['news_crawler', 'web_search', 'http_request'] },
          { name: 'text_summarizer', tool_names: ['text_summarizer', 'file_reader'] },
          { name: 'markdown_report_generator', tool_names: ['markdown_report_generator', 'file_writer'] }
        ]);
      }
    }

    loadCatalogs();
  }, []);

  useEffect(() => () => {
    if (conversationStreamRef.current) conversationStreamRef.current();
  }, []);

  useEffect(() => {
    const handleAuthExpired = () => {
      if (conversationStreamRef.current) {
        conversationStreamRef.current();
        conversationStreamRef.current = null;
      }
      setAuthUser(null);
      setAuthMessage('Phiên đăng nhập đã hết hạn. Vui lòng đăng nhập lại để tiếp tục.');
      setConversationId(null);
      setCurrentRun(null);
      setActivePlan([]);
      setExecutionLogs([]);
      setExecutionResults([]);
      setIsProcessing(false);
      setIsStreaming(false);
    };

    window.addEventListener('agentflow:auth-expired', handleAuthExpired);
    return () => window.removeEventListener('agentflow:auth-expired', handleAuthExpired);
  }, []);

  const handleAuthenticated = (user) => {
    localStorage.setItem('agentflow_user', JSON.stringify(user));
    setAuthUser(user);
    setAuthMessage('');
  };

  const handleLogout = async () => {
    await logoutUser();
    localStorage.removeItem('agentflow_user');
    setAuthUser(null);
  };

  if (backendStatus && !authUser) {
    return <AuthScreen onAuthenticated={handleAuthenticated} initialMessage={authMessage} />;
  }

  // Handle user sending message in Chat Studio
  const handleSendMessage = async (textPrompt) => {
    setIsProcessing(true);
    const sendStartTime = Date.now();
    setMessages(prev => [...prev, { sender: 'user', text: textPrompt }]);

    // Check if user is approving an existing plan with approval keywords
    const cleanInput = textPrompt.trim().toLowerCase();
    const isApprovalMessage = APPROVAL_KEYWORDS.some(kw => cleanInput === kw || cleanInput.startsWith(kw) || cleanInput.includes(kw));

    if (activePlan && activePlan.length > 0 && isApprovalMessage) {
      setMessages(prev => [...prev, { sender: 'supervisor', text: 'Kế hoạch đã được duyệt! Đang chuyển sang màn hình Realtime Execution Tracker để thực thi các Task...', duration: '0.1' }]);
      await handleApprovePlan();
      return;
    }

    let awaitingConversationStream = false;
    try {
      if (backendStatus) {
        let activeConversationId = conversationId;
        if (!activeConversationId) {
          const conversation = await createConversation('Technology research studio');
          activeConversationId = conversation.id;
          setConversationId(activeConversationId);
        }
        const accepted = await sendConversationMessage(activeConversationId, textPrompt);
        if (accepted.status === 'accepted' && accepted.turn_id) {
          awaitingConversationStream = true;
          if (conversationStreamRef.current) conversationStreamRef.current();
          conversationStreamRef.current = subscribeConversationEvents(
            activeConversationId,
            accepted.turn_id,
            (eventData) => {
              const payload = eventData.payload || {};
              if (eventData.type === 'planning_started') {
                setMessages(prev => [...prev, { sender: 'supervisor', text: 'Đã nhận yêu cầu. Supervisor đang phân tích và dựng workflow...' }]);
              } else if (eventData.type === 'workflow_draft_updated') {
                setActivePlan(payload.plan || []);
              } else if (eventData.type === 'assistant_delta' && payload.content) {
                setMessages(prev => [...prev, { sender: 'supervisor', text: payload.content }]);
              } else if (eventData.type === 'planning_completed') {
                setMessages(prev => [...prev, { sender: 'supervisor', text: 'Workflow đã được dựng xong. Bạn có thể review rồi bấm duyệt để thực thi.' }]);
                setIsProcessing(false);
                if (conversationStreamRef.current) conversationStreamRef.current();
              } else if (eventData.type === 'planning_failed') {
                setMessages(prev => [...prev, { sender: 'supervisor', text: `Không thể dựng workflow: ${payload.message || 'Lỗi không xác định.'}` }]);
                setIsProcessing(false);
                if (conversationStreamRef.current) conversationStreamRef.current();
              }
            },
            () => {
              setIsProcessing(false);
              setMessages(prev => [...prev, { sender: 'supervisor', text: 'Kết nối progress bị gián đoạn. Bạn có thể gửi lại yêu cầu.' }]);
            }
          );
        } else {
          const plan = accepted.plan || accepted.draft_plan || [];
          if (plan.length > 0) setActivePlan(plan);
          setMessages(prev => [...prev, {
            sender: 'supervisor',
            text: plan.length > 0
              ? `Tôi đã lập xong Kế hoạch DAG gồm ${plan.length} bước. Bạn có thể review và duyệt để thực thi.`
              : 'Tôi đã ghi nhận yêu cầu. Bạn hãy làm rõ thêm chi tiết nhé!',
            duration: ((Date.now() - sendStartTime) / 1000).toFixed(2)
          }]);
        }
      } else {

        // Fallback simulation mode
        setTimeout(() => {
          const simulatedPlan = [
            { id: 1, node: 'news_crawler', status: 'pending', description: `Cào dữ liệu từ URL liên quan đến: ${textPrompt}` },
            { id: 2, node: 'text_summarizer', status: 'pending', dependencies: [1], description: 'Tóm tắt nội dung cào được thành các điểm chính' },
            { id: 3, node: 'markdown_report_generator', status: 'pending', dependencies: [2], description: 'Xuất báo cáo Markdown lưu vào workspace_data/reports/' }
          ];
          setActivePlan(simulatedPlan);
          const durationSec = ((Date.now() - sendStartTime) / 1000).toFixed(2);
          setMessages(prev => [
            ...prev,
            { 
              sender: 'supervisor', 
              text: `Tôi đã lập Kế hoạch 3 bước dựa trên yêu cầu "${textPrompt}". Bạn có đồng ý bấm 'Approve & Execute' để chạy không?`,
              duration: durationSec
            }
          ]);
          setIsProcessing(false);
        }, 600);
      }
    } catch (err) {
      console.error('Error starting run:', err);
      const durationSec = ((Date.now() - sendStartTime) / 1000).toFixed(2);
      setMessages(prev => [...prev, { sender: 'supervisor', text: `Có lỗi kết nối: ${err.message}`, duration: durationSec }]);
    } finally {
      if (!awaitingConversationStream) setIsProcessing(false);
    }
  };

  // Handle User Approving Plan
  const handleApprovePlan = async () => {
    setIsProcessing(true);
    setIsStreaming(true);
    setExecutionDuration(null);
    const execStartTime = Date.now();

    // Switch view tab to Execution Runs Tracker
    setActiveTab('runs');

    appendExecutionLogs("[System] Plan approved by user. Starting LangGraph Execution Engine...");

    if (backendStatus) {
      try {
        let runId = currentRun?.run_id;
        if (!runId) {
          const steps = activePlan.map((task, index) => ({
            task_key: String(task.id || index + 1),
            name: task.node || `Task ${index + 1}`,
            description: task.description || `Research task ${index + 1}`,
            dependencies: (task.dependencies || []).map(String),
            expected_output_type: task.node?.toLowerCase().includes('report') ? 'report' : 'raw_data',
            ...(
              task.agent_id || (
                task.node && !['worker', 'generic_worker'].includes(task.node.toLowerCase())
                  ? task.node
                  : null
              )
                ? { agent_id: task.agent_id || task.node }
                : {}
            ),
            ...(task.capability ? { capability: task.capability } : {}),
            ...(task.tool_names?.length ? { tool_names: task.tool_names } : {})
          }));
          const workflow = await createWorkflow('Technology research workflow', steps, 'Generated from the research chat.');
          const userPrompt = messages
            .filter(message => message.sender === 'user')
            .map(message => message.text)
            .filter(Boolean)
            .filter(message => !APPROVAL_KEYWORDS.some(kw => message.trim().toLowerCase() === kw))
            .join('\n\n');
          const run = await createWorkflowRun(
            workflow.id,
            { user_prompt: userPrompt },
            { model_name: selectedModel, use_llm: true }
          );
          runId = run.run_id;
          setCurrentRun(run);
        } else {
          await approveRun(runId);
        }

        // Subscribe to SSE Realtime Stream
        const unsubscribe = subscribeRunSSEStream(
          runId,
          async (eventData) => {
            const eventType = eventData.legacy_type || eventData.type;
            if (eventType === 'log' && eventData.message) {
              appendExecutionLogs(eventData.message, eventData.created_at);
            } else if (eventData.logs) {
              appendExecutionLogs(eventData.logs, eventData.created_at);
            }

            if (eventType === 'task_update' && eventData.task) {
              setActivePlan(prevPlan => {
                const existing = prevPlan.find(t => t.id === eventData.task.id);
                if (existing) {
                  return prevPlan.map(t => t.id === eventData.task.id ? eventData.task : t);
                } else {
                  return [...prevPlan, eventData.task];
                }
              });
            }

            if (eventType === 'plan_update' && eventData.plan) {
              setActivePlan(eventData.plan);
            }

            if (eventType === 'results_update' && eventData.results) {
              setExecutionResults(eventData.results);
            }

            if (eventData.status === 'completed' || eventData.status === 'failed' || eventType === 'completed') {
              if (eventData.plan && eventData.plan.length > 0) setActivePlan(eventData.plan);
              if (eventData.results && eventData.results.length > 0) setExecutionResults(eventData.results);

              try {
                const latestDoc = await getRunDetails(runId);
                if (latestDoc?.plan && latestDoc.plan.length > 0) setActivePlan(latestDoc.plan);
                if (latestDoc?.result_storage && latestDoc.result_storage.length > 0) setExecutionResults(latestDoc.result_storage);
                if (latestDoc?.logs && latestDoc.logs.length > 0) {
                  setExecutionLogs(prev => {
                    const existing = new Set(
                      prev.map(log => typeof log === 'string' ? log : log?.message)
                    );
                    const missing = latestDoc.logs
                      .filter(log => !existing.has(log))
                      .map(log => ({ message: log, created_at: null }));
                    return [...prev, ...missing];
                  });
                }
                if (latestDoc) setCurrentRun(latestDoc);
              } catch (e) {
                console.warn('Error fetching final run details:', e);
              }

              const totalSec = ((Date.now() - execStartTime) / 1000).toFixed(2);
              setExecutionDuration(totalSec);
              setIsStreaming(false);
              setIsProcessing(false);
              unsubscribe();
            }
          },
          (err) => {
            console.error("SSE Stream error:", err);
            const totalSec = ((Date.now() - execStartTime) / 1000).toFixed(2);
            setExecutionDuration(totalSec);
            setIsStreaming(false);
            setIsProcessing(false);
          }
        );

      } catch (err) {
        console.error("Approve run error:", err);
        const totalSec = ((Date.now() - execStartTime) / 1000).toFixed(2);
        setExecutionDuration(totalSec);
        setIsStreaming(false);
        setIsProcessing(false);
      }
    } else {
      // Simulate real-time step execution for demo UI when backend offline
      let step = 0;
      const simulatedTasks = [...activePlan];

      const interval = setInterval(() => {
        if (step < simulatedTasks.length) {
          simulatedTasks[step] = { ...simulatedTasks[step], status: 'running' };
          setActivePlan([...simulatedTasks]);
          setExecutionLogs(prev => [...prev, `[TaskDispatcher] Dispatching Task ${step + 1} to node '${simulatedTasks[step].node}'...`]);

          setTimeout(() => {
            simulatedTasks[step] = { ...simulatedTasks[step], status: 'done' };
            setActivePlan([...simulatedTasks]);
            setExecutionLogs(prev => [...prev, `[WorkerNode] Task ${step + 1} completed with output result.`]);
            setExecutionResults(prev => [...prev, {
              task_id: step + 1,
              node: simulatedTasks[step].node,
              description: simulatedTasks[step].description,
              status: 'done',
              result: `Mock execution output result data generated for Task ${step + 1} (${simulatedTasks[step].node})`
            }]);
            step++;
          }, 1200);
        } else {
          clearInterval(interval);
          setExecutionLogs(prev => [...prev, "[TaskDispatcher] All tasks in plan finished execution successfully!"]);
          const totalSec = ((Date.now() - execStartTime) / 1000).toFixed(2);
          setExecutionDuration(totalSec);
          setIsStreaming(false);
          setIsProcessing(false);
        }
      }, 1800);
    }
  };

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100vh', width: '100vw', overflow: 'hidden' }}>
      {/* Top Header */}
      <Header 
        selectedModel={selectedModel}
        setSelectedModel={setSelectedModel}
        backendStatus={backendStatus}
        user={authUser}
        onLogout={handleLogout}
      />

      {/* Main Layout Body */}
      <div style={{ display: 'flex', flex: 1, overflow: 'hidden' }}>
        {/* Left Navigation Sidebar */}
        <Sidebar activeTab={activeTab} setActiveTab={setActiveTab} />

        {/* Dynamic Center View Tab */}
        <main style={{ flex: 1, display: 'flex', flexDirection: 'column', overflow: 'hidden', position: 'relative' }}>
          {activeTab === 'studio' && (
            <ChatStudio 
              messages={messages}
              onSendMessage={handleSendMessage}
              onApprovePlan={handleApprovePlan}
              isProcessing={isProcessing}
              activePlan={activePlan}
              selectedModel={selectedModel}
            />
          )}

          {activeTab === 'canvas' && (
            <FlowCanvas plan={activePlan} />
          )}

          {activeTab === 'catalog' && (
            <CatalogBrowser tools={tools} agents={agents} />
          )}

          {activeTab === 'runs' && (
            <ExecutionTracker 
              currentRun={currentRun}
              logs={executionLogs}
              results={executionResults}
              plan={activePlan}
              isStreaming={isStreaming}
              executionDuration={executionDuration}
            />
          )}
        </main>
      </div>
    </div>
  );
}
