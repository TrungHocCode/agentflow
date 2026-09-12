import React, { useState, useEffect } from 'react';
import Header from './components/Header';
import Sidebar from './components/Sidebar';
import ChatStudio from './components/ChatStudio';
import FlowCanvas from './components/FlowCanvas';
import CatalogBrowser from './components/CatalogBrowser';
import ExecutionTracker from './components/ExecutionTracker';

import { 
  getCatalogTools, 
  getCatalogAgents, 
  startRun, 
  sendChatMessage,
  approveRun, 
  getRunDetails,
  subscribeRunSSEStream 
} from './services/api';

export default function App() {
  const [activeTab, setActiveTab] = useState('studio');
  const [selectedModel, setSelectedModel] = useState('qwen3:8b');
  const [backendStatus, setBackendStatus] = useState(false);

  // Catalogs
  const [tools, setTools] = useState([]);
  const [agents, setAgents] = useState([]);

  // Conversational & Execution State
  const [messages, setMessages] = useState([]);
  const [currentRun, setCurrentRun] = useState(null);
  const [activePlan, setActivePlan] = useState([]);
  const [executionLogs, setExecutionLogs] = useState([]);
  const [executionResults, setExecutionResults] = useState([]);
  const [isProcessing, setIsProcessing] = useState(false);
  const [isStreaming, setIsStreaming] = useState(false);
  const [executionDuration, setExecutionDuration] = useState(null);

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

  // Handle user sending message in Chat Studio
  const handleSendMessage = async (textPrompt) => {
    setIsProcessing(true);
    const sendStartTime = Date.now();
    setMessages(prev => [...prev, { sender: 'user', text: textPrompt }]);

    // Check if user is approving an existing plan with approval keywords
    const approvalKeywords = ['ok', 'đồng ý', 'chạy đi', 'thực thi', 'bắt đầu', 'yes', 'approve', 'run', 'thực hiện đi', 'thực hiện', 'duyệt', 'tiến hành'];
    const cleanInput = textPrompt.trim().toLowerCase();
    const isApprovalMessage = approvalKeywords.some(kw => cleanInput === kw || cleanInput.startsWith(kw) || cleanInput.includes(kw));

    if (activePlan && activePlan.length > 0 && isApprovalMessage) {
      setMessages(prev => [...prev, { sender: 'supervisor', text: 'Kế hoạch đã được duyệt! Đang chuyển sang màn hình Realtime Execution Tracker để thực thi các Task...', duration: '0.1' }]);
      await handleApprovePlan();
      return;
    }

    try {
      if (backendStatus) {
        let runData;
        if (currentRun?.run_id && currentRun?.status === 'pending') {
          // Multi-turn continuation on existing run
          runData = await sendChatMessage(currentRun.run_id, textPrompt);
        } else {
          // Start a new workflow run
          runData = await startRun(textPrompt, selectedModel);
        }
        setCurrentRun(runData);

        const durationSec = ((Date.now() - sendStartTime) / 1000).toFixed(2);

        // If backend returned proposed plan
        if (runData.plan && runData.plan.length > 0) {
          setActivePlan(runData.plan);
          setMessages(prev => [
            ...prev,
            { 
              sender: 'supervisor', 
              text: `Tôi đã lập xong Kế hoạch DAG gồm ${runData.plan.length} bước bên dưới cho bạn. Bạn có thể bấm 'Duyệt & Bắt Đầu Thực Thi Workflow' hoặc gõ 'đồng ý' nhé!`,
              duration: durationSec
            }
          ]);
        } else {
          setMessages(prev => [
            ...prev,
            { 
              sender: 'supervisor', 
              text: `Tôi đã ghi nhận yêu cầu của bạn. Bạn hãy làm rõ thêm chi tiết hoặc xác nhận để tôi lập kế hoạch nhé!`,
              duration: durationSec
            }
          ]);
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
      setIsProcessing(false);
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

    setExecutionLogs(prev => [...prev, "[System] Plan approved by user. Starting LangGraph Execution Engine..."]);

    if (backendStatus && currentRun?.run_id) {
      try {
        await approveRun(currentRun.run_id);

        // Subscribe to SSE Realtime Stream
        const unsubscribe = subscribeRunSSEStream(
          currentRun.run_id,
          async (eventData) => {
            if (eventData.type === 'log' && eventData.message) {
              setExecutionLogs(prev => [...prev, eventData.message]);
            } else if (eventData.logs) {
              setExecutionLogs(prev => [...prev, ...eventData.logs]);
            }

            if (eventData.type === 'task_update' && eventData.task) {
              setActivePlan(prevPlan => {
                const existing = prevPlan.find(t => t.id === eventData.task.id);
                if (existing) {
                  return prevPlan.map(t => t.id === eventData.task.id ? eventData.task : t);
                } else {
                  return [...prevPlan, eventData.task];
                }
              });
            }

            if (eventData.type === 'plan_update' && eventData.plan) {
              setActivePlan(eventData.plan);
            }

            if (eventData.type === 'results_update' && eventData.results) {
              setExecutionResults(eventData.results);
            }

            if (eventData.status === 'completed' || eventData.status === 'failed' || eventData.type === 'completed') {
              if (eventData.plan && eventData.plan.length > 0) setActivePlan(eventData.plan);
              if (eventData.results && eventData.results.length > 0) setExecutionResults(eventData.results);

              try {
                const latestDoc = await getRunDetails(currentRun.run_id);
                if (latestDoc?.plan && latestDoc.plan.length > 0) setActivePlan(latestDoc.plan);
                if (latestDoc?.result_storage && latestDoc.result_storage.length > 0) setExecutionResults(latestDoc.result_storage);
                if (latestDoc?.logs && latestDoc.logs.length > 0) setExecutionLogs(latestDoc.logs);
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
