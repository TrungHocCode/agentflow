import React, { useState, useEffect, useRef, useCallback } from 'react';
import Header from './components/Header';
import Sidebar from './components/Sidebar';
import ChatStudio from './components/ChatStudio';
import FlowCanvas from './components/FlowCanvas';
import CatalogBrowser from './components/CatalogBrowser';
import ExecutionTracker from './components/ExecutionTracker';
import AuthScreen from './components/AuthScreen';
import RunResultsDrawer from './components/RunResultsDrawer';

import { 
  getCatalogTools, 
  getCatalogAgents, 
  getConversations,
  getRuns,
  getRunEvents,
  createConversation,
  deleteConversation,
  cancelRun,
  sendConversationMessage,
  getConversationMessages,
  subscribeConversationEvents,
  createWorkflow,
  createWorkflowRun,
  approveRun, 
  getRunDetails,
  subscribeRunSSEStream,
  logout as logoutUser
} from './services/api';

const APPROVAL_COMMANDS = new Set([
  'ok',
  'đồng ý',
  'chạy đi',
  'thực thi',
  'bắt đầu',
  'yes',
  'approve',
  'run',
  'thực hiện đi',
  'thực hiện',
  'duyệt',
  'tiến hành'
]);

const isApprovalCommand = (text) => {
  const normalized = text.trim().toLowerCase().replace(/[.!?]+$/g, '').trim();
  return APPROVAL_COMMANDS.has(normalized);
};

const ACTIVE_CONVERSATION_KEY = 'agentflow_active_conversation_id';
const ACTIVE_RUN_KEY = 'agentflow_active_run_id';
const conversationRunStorageKey = (conversationId) => `${ACTIVE_RUN_KEY}:${conversationId}`;
const ACTIVE_RUN_STATUSES = new Set(['queued', 'running']);
const TERMINAL_RUN_STATUSES = new Set(['completed', 'failed', 'cancelled', 'interrupted', 'abandoned']);

const toChatMessages = (persistedMessages = []) => persistedMessages
  .filter(message => message.role === 'user' || message.role === 'assistant')
  .map(message => ({
    sender: message.role === 'user' ? 'user' : 'supervisor',
    text: message.content,
    duration: message.metadata?.duration
  }));

export default function App() {
  const [activeTab, setActiveTab] = useState('studio');
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
  const [conversationClosed, setConversationClosed] = useState(false);
  const [currentRun, setCurrentRun] = useState(null);
  const [conversationRun, setConversationRun] = useState(null);
  const [isResultsOpen, setIsResultsOpen] = useState(false);
  const [resultsSource, setResultsSource] = useState('conversation');
  const [activePlan, setActivePlan] = useState([]);
  const [draftPlan, setDraftPlan] = useState([]);
  const [executionResults, setExecutionResults] = useState([]);
  const [isProcessing, setIsProcessing] = useState(false);
  const [isStreaming, setIsStreaming] = useState(false);
  const [isRestoringConversation, setIsRestoringConversation] = useState(false);
  const [isCancelling, setIsCancelling] = useState(false);
  const [isDeletingConversation, setIsDeletingConversation] = useState(false);
  const [executionDuration, setExecutionDuration] = useState(null);
  const [authMessage, setAuthMessage] = useState('');
  const conversationStreamRef = useRef(null);
  const conversationRunIdRef = useRef(null);
  const runStreamRef = useRef(null);
  const runStartedAtRef = useRef(null);
  const terminalRunIdsRef = useRef(new Set());
  const runVisibilitySyncRef = useRef(false);

  const handleRunStreamEvent = useCallback(async (runId, eventData, unsubscribe) => {
    const rawEventType = eventData.type;
    const eventType = eventData.legacy_type || rawEventType;
    const eventStatus = eventData.status || eventData.payload?.status;
    const terminalEvent = ['run_completed', 'run_failed', 'run_interrupted'].includes(rawEventType)
      || eventType === 'completed';
    if (terminalRunIdsRef.current.has(runId) && !terminalEvent) return;

    if (eventType === 'task_update' && eventData.task) {
      const mergeTask = previousPlan => {
        const existing = previousPlan.some(task => String(task.id) === String(eventData.task.id));
        return existing
          ? previousPlan.map(task => String(task.id) === String(eventData.task.id) ? eventData.task : task)
          : [...previousPlan, eventData.task];
      };
      setActivePlan(mergeTask);
      if (conversationRunIdRef.current === runId) setDraftPlan(mergeTask);
    }

    if (eventType === 'plan_update' && eventData.plan) {
      setActivePlan(eventData.plan);
      if (conversationRunIdRef.current === runId) setDraftPlan(eventData.plan);
    }

    if (eventType === 'results_update' && eventData.results) {
      setExecutionResults(eventData.results);
    }

    let terminalStatus = null;
    if (['run_completed', 'run_failed', 'run_interrupted'].includes(rawEventType)) {
      terminalStatus = ['completed', 'failed', 'interrupted', 'cancelled', 'abandoned'].includes(eventStatus)
        ? eventStatus
        : rawEventType === 'run_failed' ? 'failed'
          : rawEventType === 'run_interrupted' ? 'interrupted'
            : 'completed';
    } else if (eventType === 'completed'
      && ['completed', 'failed', 'interrupted', 'cancelled', 'abandoned'].includes(eventStatus)) {
      terminalStatus = eventStatus;
    }

    if (eventStatus === 'queued' || eventStatus === 'running') {
      setCurrentRun(previous => previous?.run_id === runId ? { ...previous, status: eventStatus } : previous);
      setConversationRun(previous => previous?.run_id === runId ? { ...previous, status: eventStatus } : previous);
    }

    if (!terminalStatus) return;
    terminalRunIdsRef.current.add(runId);

    if (eventData.plan?.length) {
      setActivePlan(eventData.plan);
      if (conversationRunIdRef.current === runId) setDraftPlan(eventData.plan);
    }
    if (eventData.results?.length) setExecutionResults(eventData.results);
    setCurrentRun(previous => previous?.run_id === runId
      ? { ...previous, status: terminalStatus }
      : previous);
    setConversationRun(previous => previous?.run_id === runId
      ? { ...previous, status: terminalStatus }
      : previous);

    try {
      const latestRun = await getRunDetails(runId);
      if (latestRun?.run_id) {
        setCurrentRun(previous => previous?.run_id === runId ? latestRun : previous);
        setConversationRun(previous => previous?.run_id === runId ? latestRun : previous);
        setActivePlan(latestRun.plan || []);
        if (conversationRunIdRef.current === runId) setDraftPlan(latestRun.plan || []);
        setExecutionResults(latestRun.result_storage || []);
        setExecutionDuration(latestRun.execution_time_ms
          ? (latestRun.execution_time_ms / 1000).toFixed(2)
          : null);
      }
    } catch (error) {
      console.warn('Could not refresh final run details:', error);
      setExecutionDuration(runStartedAtRef.current
        ? ((Date.now() - runStartedAtRef.current) / 1000).toFixed(2)
        : null);
    }

    setIsStreaming(false);
    setIsProcessing(false);
    if (runStreamRef.current === unsubscribe) runStreamRef.current = null;
    unsubscribe();
  }, []);

  const subscribeToRun = useCallback((runId, lastEventId = null) => {
    if (runStreamRef.current) runStreamRef.current();
    let closed = false;
    let sourceUnsubscribe = () => {};
    let reconnectTimer = null;
    let retryCount = 0;
    let lastReceivedEventId = lastEventId;

    const unsubscribe = () => {
      if (closed) return;
      closed = true;
      if (reconnectTimer !== null) window.clearTimeout(reconnectTimer);
      sourceUnsubscribe();
      if (runStreamRef.current === unsubscribe) runStreamRef.current = null;
    };

    const connect = () => {
      if (closed) return;
      sourceUnsubscribe = subscribeRunSSEStream(
        runId,
        eventData => {
          if (eventData.event_id) lastReceivedEventId = eventData.event_id;
          retryCount = 0;
          void handleRunStreamEvent(runId, eventData, unsubscribe);
        },
        error => {
          if (closed || terminalRunIdsRef.current.has(runId)) return;
          console.warn('Run progress connection interrupted; reconnecting:', error);
          if (/^(401|403|404):/.test(error.message)) {
            if (conversationRunIdRef.current === runId) {
              setIsStreaming(false);
              setIsProcessing(false);
            }
            unsubscribe();
            return;
          }

          if (conversationRunIdRef.current === runId) {
            setIsStreaming(true);
            setIsProcessing(true);
          }
          const delay = Math.min(30_000, 500 * (2 ** Math.min(retryCount, 6)));
          retryCount += 1;
          reconnectTimer = window.setTimeout(connect, delay);
        },
        lastReceivedEventId
      );
    };

    runStreamRef.current = unsubscribe;
    connect();
    return unsubscribe;
  }, [handleRunStreamEvent]);

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
          {
            name: 'news_crawler',
            description: 'Extracts article content and renders JavaScript pages when needed.'
          },
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
    if (runStreamRef.current) runStreamRef.current();
  }, []);

  useEffect(() => {
    if (conversationId) {
      localStorage.setItem(ACTIVE_CONVERSATION_KEY, conversationId);
    }
  }, [conversationId]);

  useEffect(() => {
    if (currentRun?.run_id) localStorage.setItem(ACTIVE_RUN_KEY, currentRun.run_id);
  }, [currentRun]);

  useEffect(() => {
    if (!authUser) return undefined;
    let cancelled = false;
    setIsRestoringConversation(true);

    const restoreLatestConversation = async () => {
      try {
        const conversations = await getConversations();
        if (cancelled) return;
        const sortedConversations = [...conversations]
          .filter(conversation => conversation.status !== 'archived')
          .sort((left, right) => new Date(right.updated_at) - new Date(left.updated_at));
        const rememberedId = localStorage.getItem(ACTIVE_CONVERSATION_KEY);
        const conversation = sortedConversations.find(item => item.id === rememberedId)
          || sortedConversations[0]
          || null;
        if (!conversation) localStorage.removeItem(ACTIVE_CONVERSATION_KEY);
        const persistedMessages = conversation
          ? await getConversationMessages(conversation.id)
          : [];
        if (cancelled) return;

        let runs = [];
        try {
          runs = await getRuns();
        } catch (error) {
          console.warn('Could not restore workflow history:', error);
        }

        const relatedRuns = conversation
          ? runs
            .filter(run => run.conversation_id === conversation.id)
            .sort((left, right) => new Date(right.updated_at) - new Date(left.updated_at))
          : [];
        let linkedRun = null;
        const rememberedRunId = localStorage.getItem(ACTIVE_RUN_KEY);
        const rememberedConversationRunId = conversation
          ? localStorage.getItem(conversationRunStorageKey(conversation.id))
          : null;
        const loadRunIfAvailable = async (runId) => {
          if (!runId) return null;
          try {
            return await getRunDetails(runId);
          } catch (error) {
            console.warn('Could not restore workflow status:', error);
            return null;
          }
        };
        const conversationRunSnapshot = await loadRunIfAvailable(rememberedConversationRunId);
        const rememberedRun = rememberedRunId === rememberedConversationRunId
          ? conversationRunSnapshot
          : await loadRunIfAvailable(rememberedRunId);
        const latestRelatedRun = !conversationRunSnapshot && relatedRuns[0]?.run_id
          ? await loadRunIfAvailable(relatedRuns[0].run_id)
          : null;
        if (conversationRunSnapshot) {
          linkedRun = conversationRunSnapshot;
        } else if (rememberedRun?.conversation_id === conversation?.id) {
          linkedRun = rememberedRun;
        } else if (latestRelatedRun) {
          linkedRun = latestRelatedRun;
        } else if (!conversation) {
          linkedRun = rememberedRun;
        }
        if (cancelled) return;

        const conversationRun = linkedRun && (
          linkedRun.conversation_id === conversation?.id
          || linkedRun.run_id === rememberedConversationRunId
        )
          ? linkedRun
          : null;
        const restoredPlan = linkedRun?.plan?.length
          ? linkedRun.plan
          : (conversation?.draft_plan || []);
        setConversationId(conversation?.id || null);
        if (conversation) localStorage.setItem(ACTIVE_CONVERSATION_KEY, conversation.id);
        setMessages(toChatMessages(persistedMessages));
        setCurrentRun(linkedRun);
        setConversationRun(conversationRun);
        setConversationClosed(Boolean(conversationRun));
        conversationRunIdRef.current = conversationRun?.run_id || null;
        setActivePlan(restoredPlan);
        setDraftPlan(conversationRun || !linkedRun ? restoredPlan : (conversation?.draft_plan || []));
        setExecutionResults(linkedRun?.result_storage || []);
        setExecutionDuration(linkedRun?.execution_time_ms
          ? (linkedRun.execution_time_ms / 1000).toFixed(2)
          : null);
        if (!linkedRun) {
          if (!rememberedRun || TERMINAL_RUN_STATUSES.has(rememberedRun.status)) {
            localStorage.removeItem(ACTIVE_RUN_KEY);
          }
          if (conversation) {
            localStorage.removeItem(conversationRunStorageKey(conversation.id));
          }
        } else if (conversationRun) {
          localStorage.setItem(conversationRunStorageKey(conversation.id), linkedRun.run_id);
        }

        let runToTrack = linkedRun;
        const runIsActive = linkedRun && ACTIVE_RUN_STATUSES.has(linkedRun.status);
        if (linkedRun && TERMINAL_RUN_STATUSES.has(linkedRun.status)) {
          terminalRunIdsRef.current.add(linkedRun.run_id);
        }
        setIsStreaming(Boolean(runIsActive));
        setIsProcessing(Boolean(runIsActive));
        if (runIsActive) {
          runStartedAtRef.current = Date.now();
          let lastEventId = null;
          try {
            const events = await getRunEvents(linkedRun.run_id);
            lastEventId = events.at(-1)?.event_id || null;
          } catch (error) {
            console.warn('Could not load previous workflow events:', error);
          }
          try {
            const refreshedRun = await getRunDetails(linkedRun.run_id);
            if (refreshedRun?.run_id) {
              runToTrack = refreshedRun;
              setCurrentRun(refreshedRun);
              if (conversationRunIdRef.current === linkedRun.run_id) {
                setConversationRun(refreshedRun);
                setDraftPlan(refreshedRun.plan || []);
              }
              setActivePlan(refreshedRun.plan || []);
              setExecutionResults(refreshedRun.result_storage || []);
              setExecutionDuration(refreshedRun.execution_time_ms
                ? (refreshedRun.execution_time_ms / 1000).toFixed(2)
                : null);
            }
          } catch (error) {
            console.warn('Could not refresh workflow status before reconnecting:', error);
          }
          if (!cancelled && ACTIVE_RUN_STATUSES.has(runToTrack.status)) {
            subscribeToRun(linkedRun.run_id, lastEventId);
          } else if (!cancelled) {
            terminalRunIdsRef.current.add(linkedRun.run_id);
            setIsStreaming(false);
            setIsProcessing(false);
          }
        }
        if (!conversation && linkedRun) setActiveTab('runs');
      } catch (error) {
        if (!cancelled) console.warn('Could not restore previous conversation:', error);
      }
    };

    void restoreLatestConversation().finally(() => {
      if (!cancelled) setIsRestoringConversation(false);
    });
    return () => {
      cancelled = true;
      if (runStreamRef.current) {
        runStreamRef.current();
        runStreamRef.current = null;
      }
    };
  }, [authUser, subscribeToRun]);

  useEffect(() => {
    const resumeVisibleRun = async () => {
      const runId = currentRun?.run_id;
      if (
        document.visibilityState !== 'visible'
        || !runId
        || !ACTIVE_RUN_STATUSES.has(currentRun.status)
        || runVisibilitySyncRef.current
      ) return;

      runVisibilitySyncRef.current = true;
      try {
        const [latestRun, events] = await Promise.all([
          getRunDetails(runId),
          getRunEvents(runId)
        ]);
        if (localStorage.getItem(ACTIVE_RUN_KEY) !== runId) return;

        setCurrentRun(previous => previous?.run_id === runId ? latestRun : previous);
        if (conversationRunIdRef.current === runId) {
          setConversationRun(previous => previous?.run_id === runId ? latestRun : previous);
          setDraftPlan(latestRun.plan || []);
        }
        setActivePlan(latestRun.plan || []);
        setExecutionResults(latestRun.result_storage || []);
        setExecutionDuration(latestRun.execution_time_ms
          ? (latestRun.execution_time_ms / 1000).toFixed(2)
          : null);

        if (ACTIVE_RUN_STATUSES.has(latestRun.status)) {
          setIsStreaming(true);
          setIsProcessing(true);
          subscribeToRun(runId, events.at(-1)?.event_id || null);
        } else {
          terminalRunIdsRef.current.add(runId);
          setIsStreaming(false);
          setIsProcessing(false);
        }
      } catch (error) {
        console.warn('Could not resume workflow updates after returning to the page:', error);
      } finally {
        runVisibilitySyncRef.current = false;
      }
    };

    document.addEventListener('visibilitychange', resumeVisibleRun);
    window.addEventListener('pageshow', resumeVisibleRun);
    return () => {
      document.removeEventListener('visibilitychange', resumeVisibleRun);
      window.removeEventListener('pageshow', resumeVisibleRun);
    };
  }, [currentRun?.run_id, currentRun?.status, subscribeToRun]);

  useEffect(() => {
    const handleAuthExpired = () => {
      if (conversationStreamRef.current) {
        conversationStreamRef.current();
        conversationStreamRef.current = null;
      }
      if (runStreamRef.current) {
        runStreamRef.current();
        runStreamRef.current = null;
      }
      setAuthUser(null);
      setAuthMessage('Phiên đăng nhập đã hết hạn. Vui lòng đăng nhập lại để tiếp tục.');
      setConversationId(null);
      setConversationClosed(false);
      setCurrentRun(null);
      setConversationRun(null);
      setIsResultsOpen(false);
      conversationRunIdRef.current = null;
      setActivePlan([]);
      setDraftPlan([]);
      setMessages([]);
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
    const startsNewConversation = conversationClosed;
    if (startsNewConversation) {
      setMessages([{ sender: 'user', text: textPrompt }]);
      setConversationId(null);
      setConversationClosed(false);
      setCurrentRun(null);
      localStorage.removeItem(ACTIVE_RUN_KEY);
      setDraftPlan([]);
      setActivePlan([]);
      setConversationRun(null);
      setIsResultsOpen(false);
      conversationRunIdRef.current = null;
    } else {
      setMessages(prev => [...prev, { sender: 'user', text: textPrompt }]);
    }

    // Only a standalone confirmation command may approve a pending plan.
    const isApprovalMessage = isApprovalCommand(textPrompt);

    if (!startsNewConversation && draftPlan.length > 0 && isApprovalMessage) {
      setMessages(prev => [...prev, { sender: 'supervisor', text: 'Kế hoạch đã được duyệt. Bạn có thể theo dõi tiến độ ngay trong hội thoại và mở kết quả sau khi quy trình hoàn tất.', duration: '0.1' }]);
      await handleApprovePlan();
      return;
    }

    let awaitingConversationStream = false;
    try {
      if (backendStatus) {
        let activeConversationId = startsNewConversation ? null : conversationId;
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
              setMessages(prev => [...prev, { sender: 'supervisor', text: 'Đã nhận yêu cầu. Đang xử lý...' }]);
              } else if (eventData.type === 'workflow_draft_updated') {
                const plan = payload.plan || [];
                setActivePlan(plan);
                setDraftPlan(plan);
              } else if (eventData.type === 'assistant_delta' && payload.content) {
                setMessages(prev => [...prev, { sender: 'supervisor', text: payload.content }]);
              } else if (eventData.type === 'planning_completed') {
                if (payload.outcome === 'propose_plan' || !payload.outcome) {
                  setMessages(prev => [...prev, { sender: 'supervisor', text: 'Quy trình đã sẵn sàng. Bạn có thể xem lại kế hoạch rồi chọn bắt đầu.' }]);
                }
                setIsProcessing(false);
                if (conversationStreamRef.current) conversationStreamRef.current();
              } else if (eventData.type === 'planning_failed') {
                setMessages(prev => [...prev, { sender: 'supervisor', text: `Không thể chuẩn bị quy trình: ${payload.message || 'Lỗi không xác định.'}` }]);
                setIsProcessing(false);
                if (conversationStreamRef.current) conversationStreamRef.current();
              }
            },
            () => {
              setIsProcessing(false);
              setMessages(prev => [...prev, { sender: 'supervisor', text: 'Kết nối theo dõi bị gián đoạn. Bạn có thể kiểm tra tiến độ trong Lịch sử chạy.' }]);
            }
          );
        } else {
          const plan = accepted.plan || accepted.draft_plan || [];
          setActivePlan(plan);
          setDraftPlan(plan);
          const conversationMessages = await getConversationMessages(activeConversationId);
          const latestAssistantMessage = [...conversationMessages]
            .reverse()
            .find(message => message.role === 'assistant')?.content;
          const decision = accepted.metadata?.supervisor_decision;
          setMessages(prev => [...prev, {
            sender: 'supervisor',
            text: latestAssistantMessage || (decision === 'propose_plan'
              ? `Tôi đã chuẩn bị kế hoạch gồm ${plan.length} bước. Bạn có thể xem lại rồi chọn bắt đầu.`
              : decision === 'answer'
                ? 'Tôi đã trả lời yêu cầu của bạn.'
                : 'Tôi đã ghi nhận yêu cầu. Bạn hãy làm rõ thêm chi tiết nhé!'),
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
          setDraftPlan(simulatedPlan);
          const durationSec = ((Date.now() - sendStartTime) / 1000).toFixed(2);
          setMessages(prev => [
            ...prev,
            { 
              sender: 'supervisor', 
              text: `Tôi đã lập kế hoạch 3 bước dựa trên yêu cầu "${textPrompt}". Bạn có muốn duyệt và bắt đầu không?`,
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
    setExecutionResults([]);
    const execStartTime = Date.now();

    if (backendStatus) {
      try {
        const linkedConversationId = conversationId;
        const pendingRunId = currentRun?.approval_status === 'pending' && currentRun?.conversation_id === linkedConversationId
          ? currentRun.run_id
          : null;
        let runId = pendingRunId;
        if (!pendingRunId) {
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
            .filter(message => !isApprovalCommand(message))
            .join('\n\n');
          const run = await createWorkflowRun(
            workflow.id,
            { user_prompt: userPrompt },
            { use_llm: true },
            linkedConversationId
          );
          runId = run.run_id;
          setCurrentRun(run);
          setConversationRun(run);
        } else {
          const approvedRun = await approveRun(runId);
          setCurrentRun(approvedRun);
          setConversationRun(approvedRun);
        }
        conversationRunIdRef.current = runId;
        localStorage.setItem(ACTIVE_RUN_KEY, runId);
        if (linkedConversationId) {
          localStorage.setItem(conversationRunStorageKey(linkedConversationId), runId);
        }

        // Retain the approved plan in Chat Studio and mark this conversation as
        // closed for planning; the next user prompt will start a fresh chat.
        setConversationClosed(true);
        runStartedAtRef.current = execStartTime;
        subscribeToRun(runId);

      } catch (err) {
        console.error("Approve run error:", err);
        const totalSec = ((Date.now() - execStartTime) / 1000).toFixed(2);
        setExecutionDuration(totalSec);
        setIsStreaming(false);
        setIsProcessing(false);
      }
    } else {
      // Simulate real-time step execution for demo UI when backend offline
      setConversationRun({ run_id: null, status: 'running', conversation_id: conversationId });
      setConversationClosed(true);
      let step = 0;
      const simulatedTasks = [...activePlan];

      const interval = setInterval(() => {
        if (step < simulatedTasks.length) {
          simulatedTasks[step] = { ...simulatedTasks[step], status: 'running' };
          setActivePlan([...simulatedTasks]);
          setDraftPlan([...simulatedTasks]);

          setTimeout(() => {
            simulatedTasks[step] = { ...simulatedTasks[step], status: 'done' };
            setActivePlan([...simulatedTasks]);
            setDraftPlan([...simulatedTasks]);
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
          setConversationRun(prev => prev ? { ...prev, status: 'completed' } : prev);
          const totalSec = ((Date.now() - execStartTime) / 1000).toFixed(2);
          setExecutionDuration(totalSec);
          setIsStreaming(false);
          setIsProcessing(false);
        }
      }, 1800);
    }
  };

  const handleCancelRun = async () => {
    const runToCancel = conversationRun && ACTIVE_RUN_STATUSES.has(conversationRun.status)
      ? conversationRun
      : currentRun;
    const runId = runToCancel?.run_id;
    if (!runId || !ACTIVE_RUN_STATUSES.has(runToCancel.status)) return;

    setIsCancelling(true);
    try {
      const cancelledRun = await cancelRun(runId);
      if (TERMINAL_RUN_STATUSES.has(cancelledRun.status)) {
        terminalRunIdsRef.current.add(runId);
      }
      setCurrentRun(cancelledRun);
      setConversationRun(previous => previous?.run_id === runId ? cancelledRun : previous);
      setActivePlan(cancelledRun.plan || activePlan);
      if (conversationRunIdRef.current === runId) setDraftPlan(cancelledRun.plan || draftPlan);
      setIsStreaming(false);
      setIsProcessing(false);
      if (conversationId) {
        setMessages(previous => [...previous, {
          sender: 'supervisor',
          text: 'Đã yêu cầu dừng workflow. Nếu một bước đang xử lý, hệ thống có thể cần hoàn tất bước đó trước khi dừng hẳn.'
        }]);
      }
    } catch (error) {
      if (conversationId) {
        setMessages(previous => [...previous, {
          sender: 'supervisor',
          text: `Không thể hủy workflow: ${error.message}`
        }]);
      }
    } finally {
      setIsCancelling(false);
    }
  };

  const handleDeleteConversation = async () => {
    if (!conversationId || isDeletingConversation || (isProcessing && !isStreaming)) return;
    const linkedRun = conversationRun;
    const linkedRunIsActive = Boolean(linkedRun && ACTIVE_RUN_STATUSES.has(linkedRun.status));
    const warning = linkedRunIsActive
          ? 'Hội thoại sẽ bị xóa vĩnh viễn. Quy trình đang chạy sẽ không bị hủy và vẫn có thể theo dõi trong Lịch sử chạy. Tiếp tục?'
      : 'Xóa vĩnh viễn hội thoại này và các tin nhắn của nó?';
    if (!window.confirm(warning)) return;

    setIsDeletingConversation(true);
    try {
      await deleteConversation(conversationId);
      localStorage.removeItem(ACTIVE_CONVERSATION_KEY);
      localStorage.removeItem(conversationRunStorageKey(conversationId));
      setConversationId(null);
      setConversationClosed(false);
      setConversationRun(null);
      setDraftPlan([]);
      setMessages([]);
      setIsResultsOpen(false);
      conversationRunIdRef.current = null;

      if (linkedRun) {
        setCurrentRun(linkedRun);
        setActivePlan(linkedRun.plan || []);
        setExecutionResults(linkedRun.result_storage || []);
        if (linkedRunIsActive) setActiveTab('runs');
      } else {
        localStorage.removeItem(ACTIVE_RUN_KEY);
        setCurrentRun(null);
        setActivePlan([]);
        setExecutionResults([]);
        setExecutionDuration(null);
      }
      setIsProcessing(false);
      if (!linkedRunIsActive && runStreamRef.current) {
        runStreamRef.current();
        runStreamRef.current = null;
      }
    } catch (error) {
      setMessages(previous => [...previous, {
        sender: 'supervisor',
        text: `Không thể xóa hội thoại: ${error.message}`
      }]);
    } finally {
      setIsDeletingConversation(false);
    }
  };

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100vh', width: '100vw', overflow: 'hidden' }}>
      {/* Top Header */}
      <Header 
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
              onCancelRun={handleCancelRun}
              onDeleteConversation={handleDeleteConversation}
              conversationId={conversationId}
              isCancelling={isCancelling}
              isDeletingConversation={isDeletingConversation}
              isProcessing={isProcessing}
              isStreaming={isStreaming}
              isRestoring={isRestoringConversation}
              activePlan={conversationId ? draftPlan : []}
              runStatus={conversationRun?.status}
              runId={conversationRun?.run_id}
              onOpenResults={() => {
                setResultsSource('conversation');
                setIsResultsOpen(true);
              }}
              onViewRunDetails={() => setActiveTab('runs')}
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
              results={executionResults}
              plan={activePlan}
              isStreaming={isStreaming}
              executionDuration={executionDuration}
              onCancelRun={handleCancelRun}
              isCancelling={isCancelling}
              onOpenResults={() => {
                setResultsSource('tracker');
                setIsResultsOpen(true);
              }}
            />
          )}
        </main>
      </div>
      <RunResultsDrawer
        open={isResultsOpen}
        onClose={() => setIsResultsOpen(false)}
        runId={resultsSource === 'tracker' ? currentRun?.run_id : conversationRun?.run_id}
        runStatus={resultsSource === 'tracker' ? currentRun?.status : conversationRun?.status}
        results={executionResults}
        onViewRunDetails={() => {
          setIsResultsOpen(false);
          setActiveTab('runs');
        }}
      />
    </div>
  );
}
