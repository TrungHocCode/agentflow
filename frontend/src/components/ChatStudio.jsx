import { useEffect, useMemo, useState } from 'react';
import {
  AlertCircle,
  ArrowDownRight,
  ArrowRight,
  ArrowUpRight,
  Bot,
  Clock3,
  FileText,
  GitMerge,
  MessageSquareText,
  Play,
  Sparkles,
  Trash2,
  UserRound,
  XCircle
} from 'lucide-react';
import StructuredText from './StructuredText';
import WorkflowProgressGraph from './WorkflowProgressGraph';
import { Attachment, AttachmentAction, AttachmentActions, AttachmentContent, AttachmentDescription, AttachmentMedia, AttachmentTitle } from './ui/attachment';
import { Badge } from './ui/badge';
import { Button } from './ui/button';
import { Bubble, BubbleContent } from './ui/bubble';
import { Card, CardContent, CardDescription, CardFooter, CardHeader, CardTitle } from './ui/card';
import { InputGroup, InputGroupAddon, InputGroupTextarea } from './ui/input-group';
import { Marker, MarkerContent, MarkerIcon } from './ui/marker';
import { Message, MessageAvatar, MessageContent, MessageFooter, MessageGroup, MessageHeader } from './ui/message';
import { Spinner } from './ui/spinner';

const RUN_STATUS = {
  queued: { label: 'Queued', tone: 'pending' },
  running: { label: 'Running', tone: 'running' },
  completed: { label: 'Completed', tone: 'done' },
  failed: { label: 'Failed', tone: 'failed' },
  interrupted: { label: 'Interrupted', tone: 'partial' },
  cancelled: { label: 'Cancelled', tone: 'skipped' },
  abandoned: { label: 'Stopped', tone: 'skipped' }
};

const PROMPT_SUGGESTIONS = [
  'Find the top stories on Hacker News, summarize them, and create a Markdown report.',
  'Compare LLMs using benchmarks, prioritizing official sources.',
  'Research a technology topic and present the findings with tables and charts.'
];

function groupMessages(messages) {
  return messages.reduce((groups, message, index) => {
    const lastGroup = groups.at(-1);
    if (lastGroup?.sender === message.sender) {
      lastGroup.messages.push({ ...message, renderKey: `${message.turnId || 'message'}-${index}` });
    } else {
      groups.push({
        sender: message.sender,
        messages: [{ ...message, renderKey: `${message.turnId || 'message'}-${index}` }]
      });
    }
    return groups;
  }, []);
}

export default function ChatStudio({
  messages,
  onSendMessage,
  onApprovePlan,
  onOpenResults,
  onViewRunDetails,
  onCancelRun,
  onCancelTurn,
  onDeleteConversation,
  conversationId,
  isCancelling,
  isDeletingConversation,
  isRestoring,
  isProcessing,
  isStreaming,
  activeTurnId,
  activePlan,
  runStatus,
  runId
}) {
  const [inputPrompt, setInputPrompt] = useState('');
  const [liveThinkingSeconds, setLiveThinkingSeconds] = useState(0);
  const hasStreamingAssistantMessage = messages.some(message => message.isGenerating);
  const messageGroups = useMemo(() => groupMessages(messages), [messages]);
  const status = RUN_STATUS[runStatus];

  useEffect(() => {
    if (!isProcessing) {
      setLiveThinkingSeconds(0);
      return undefined;
    }
    const startedAt = Date.now();
    setLiveThinkingSeconds(0);
    const timer = window.setInterval(() => {
      setLiveThinkingSeconds(((Date.now() - startedAt) / 1000).toFixed(1));
    }, 250);
    return () => window.clearInterval(timer);
  }, [isProcessing]);

  const handleSubmit = (event) => {
    event.preventDefault();
    if (!inputPrompt.trim() || isProcessing || isRestoring) return;
    onSendMessage(inputPrompt.trim());
    setInputPrompt('');
  };

  const handleComposerKeyDown = (event) => {
    if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) {
      event.preventDefault();
      event.currentTarget.form?.requestSubmit();
    }
  };

  return (
    <section className="studio-page" aria-labelledby="studio-title">
      <div className="studio-heading">
        <div className="page-heading__copy">
          <p className="page-eyebrow">RESEARCH STUDIO</p>
          <h1 id="studio-title" className="page-title">Start your research with a question</h1>
          <p className="page-description">Describe what you want to learn. AgentFlow will suggest a plan for you to review before it runs.</p>
        </div>
        {conversationId && (
          <Button
            type="button"
            variant="outline"
            size="sm"
            onClick={onDeleteConversation}
            disabled={isDeletingConversation || isRestoring || (isProcessing && !isStreaming)}
            title={isRestoring ? 'Restoring conversation' : 'Delete conversation'}
          >
            <Trash2 data-icon="inline-start" />
            <span className="delete-conversation-label">{isDeletingConversation ? 'Deleting…' : 'Delete conversation'}</span>
          </Button>
        )}
      </div>

      <Card className="chat-workspace">
        <header className="chat-workspace__header">
          <div className="chat-workspace__identity">
            <div className="chat-assistant-mark" aria-hidden="true"><Sparkles /></div>
            <div>
              <p className="chat-workspace__title">Research assistant</p>
              <p className="chat-workspace__subtitle">Discover · synthesize · report</p>
            </div>
          </div>
          {status && (
            <Badge variant={status.tone === 'failed' ? 'destructive' : 'secondary'} className={`run-status run-status--${status.tone}`}>
              {status.tone === 'running' ? <Spinner data-icon="inline-start" /> : null}
              {status.label}
            </Badge>
          )}
        </header>

        <div className="chat-thread" role="log" aria-label="Research conversation" aria-live="polite" aria-busy={isProcessing}>
          {isRestoring && messages.length === 0 ? (
            <Marker role="status" className="chat-status-row">
              <MarkerIcon><Spinner /></MarkerIcon>
              <MarkerContent>Restoring conversation and checking progress…</MarkerContent>
            </Marker>
          ) : messages.length === 0 ? (
            <div className="chat-empty">
              <div className="chat-empty__mark" aria-hidden="true"><MessageSquareText /></div>
              <h2>What would you like to explore today?</h2>
              <p>
                Start with a question or research goal. You can review the plan before letting the agents run it.
              </p>
              <div className="prompt-suggestions" aria-label="Suggested research questions">
                {PROMPT_SUGGESTIONS.map((suggestion) => (
                  <Button
                    key={suggestion}
                    type="button"
                    variant="outline"
                    className="prompt-suggestion"
                    disabled={isRestoring}
                    onClick={() => setInputPrompt(suggestion)}
                  >
                    <span>{suggestion}</span>
                    <ArrowRight aria-hidden="true" />
                  </Button>
                ))}
              </div>
            </div>
          ) : (
            messageGroups.map((group, groupIndex) => (
              <MessageGroup key={`${group.sender}-${groupIndex}`} className="chat-message-group">
                {group.messages.map((message) => {
                  const isUser = message.sender === 'user';
                  const isError = Boolean(message.isError);
                  return (
                    <Message key={message.renderKey} align={isUser ? 'end' : 'start'} className="chat-message">
                      <MessageAvatar>
                        <span className="chat-message__avatar" aria-hidden="true">
                          {isUser ? <UserRound /> : <Bot />}
                        </span>
                      </MessageAvatar>
                      <MessageContent>
                        <MessageHeader className="chat-message__header">
                          <span>{isUser ? 'You' : 'Research assistant'}</span>
                          {message.isGenerating && activeTurnId && (
                            <Button
                              type="button"
                              variant="ghost"
                              size="xs"
                              onClick={onCancelTurn}
                              disabled={isCancelling}
                            >
                              <XCircle data-icon="inline-start" />{isCancelling ? 'Cancelling…' : 'Cancel'}
                            </Button>
                          )}
                        </MessageHeader>
                        <Bubble
                          variant={isError ? 'destructive' : isUser ? 'default' : 'secondary'}
                          align={isUser ? 'end' : 'start'}
                          className={`chat-bubble ${isUser ? 'chat-bubble--user' : 'chat-bubble--assistant'}${isError ? ' chat-bubble--error' : ''}`}
                        >
                          <BubbleContent>
                            {message.text ? (
                              <>
                                <StructuredText text={message.text} />
                                {message.isGenerating && <span className="streaming-caret" aria-hidden="true">▍</span>}
                              </>
                            ) : message.isGenerating ? (
                              <Marker role="status" className="chat-status-row">
                                <MarkerIcon><Spinner /></MarkerIcon>
                                <MarkerContent>Preparing a response…</MarkerContent>
                              </Marker>
                            ) : null}
                            {isError && (
                              <p className="chat-error-reference">
                                <AlertCircle />{message.errorId ? `Reference ID: ${message.errorId}` : 'The response did not complete'}
                              </p>
                            )}
                          </BubbleContent>
                        </Bubble>
                        {message.duration && (
                          <MessageFooter className="chat-message__meta">
                            <Clock3 aria-hidden="true" />Response time: {message.duration}s
                          </MessageFooter>
                        )}
                      </MessageContent>
                    </Message>
                  );
                })}
              </MessageGroup>
            ))
          )}

          {isProcessing && !isStreaming && !hasStreamingAssistantMessage && (
            <Marker role="status" className="chat-status-row">
              <MarkerIcon><Spinner /></MarkerIcon>
              <MarkerContent>
                {isRestoring ? 'Restoring workflow' : 'Analyzing your request'} · {liveThinkingSeconds}s
              </MarkerContent>
              {activeTurnId && (
                <Button type="button" variant="ghost" size="sm" onClick={onCancelTurn} disabled={isCancelling}>
                  <XCircle data-icon="inline-start" />{isCancelling ? 'Cancelling…' : 'Cancel'}
                </Button>
              )}
            </Marker>
          )}

          {isStreaming && !runStatus && (
            <Marker role="status" className="chat-status-row">
              <MarkerIcon><Spinner /></MarkerIcon>
              <MarkerContent>Running research steps · {liveThinkingSeconds}s</MarkerContent>
            </Marker>
          )}

          {activePlan?.length > 0 && (
            <Card className="chat-plan-card">
              <CardHeader>
                <div className="chat-plan-heading">
                  <span className="chat-plan-heading__icon" aria-hidden="true"><GitMerge /></span>
                  <div>
                    <CardTitle>{runStatus ? 'Research workflow' : 'Proposed plan'}</CardTitle>
                    <CardDescription>
                      {runStatus ? 'Track the progress of each step here.' : 'Review the steps before starting.'}
                    </CardDescription>
                  </div>
                </div>
                <Badge variant={status?.tone === 'failed' ? 'destructive' : 'secondary'} className={status ? `run-status run-status--${status.tone}` : 'run-status run-status--pending'}>
                  {status?.label || 'Awaiting your approval'}
                </Badge>
              </CardHeader>

              <CardContent>
                <WorkflowProgressGraph tasks={activePlan} runStatus={runStatus} />
                {runStatus === 'completed' && (
                  <Attachment state="done" size="sm" className="chat-attachment">
                    <AttachmentMedia><FileText /></AttachmentMedia>
                    <AttachmentContent>
                      <AttachmentTitle>Research results are ready</AttachmentTitle>
                      <AttachmentDescription>Open the report and download the output files</AttachmentDescription>
                    </AttachmentContent>
                    <AttachmentActions>
                      <AttachmentAction type="button" aria-label="Open research results" title="Open results" onClick={onOpenResults}>
                        <ArrowUpRight />
                      </AttachmentAction>
                    </AttachmentActions>
                  </Attachment>
                )}
              </CardContent>

              <CardFooter className="chat-plan-actions">
                {!runStatus ? (
                  <Button type="button" onClick={onApprovePlan} disabled={isProcessing}>
                    <Play data-icon="inline-start" />Approve and start
                  </Button>
                ) : (
                  <>
                    <Button type="button" variant="outline" onClick={onOpenResults}>
                      <FileText data-icon="inline-start" />{runStatus === 'completed' ? 'View results and download files' : 'View results'}
                    </Button>
                    {runId && (
                      <Button type="button" variant="outline" onClick={onViewRunDetails}>
                        <ArrowDownRight data-icon="inline-start" />View detailed progress
                      </Button>
                    )}
                    {['queued', 'running'].includes(runStatus) && (
                      <Button type="button" variant="destructive" onClick={onCancelRun} disabled={isCancelling}>
                        <XCircle data-icon="inline-start" />{isCancelling ? 'Cancelling…' : 'Cancel workflow'}
                      </Button>
                    )}
                  </>
                )}
              </CardFooter>
            </Card>
          )}
        </div>

        <form onSubmit={handleSubmit} className="chat-composer">
          <InputGroup className="chat-input-group">
            <InputGroupTextarea
              value={inputPrompt}
              onChange={event => setInputPrompt(event.target.value)}
              onKeyDown={handleComposerKeyDown}
              placeholder="What would you like to research or synthesize?"
              aria-label="Research request"
              disabled={isProcessing || isRestoring}
              rows={2}
            />
            <InputGroupAddon align="block-end" className="chat-composer__footer">
              <span className="chat-composer__hint">Press Enter to send · Shift + Enter for a new line</span>
              <Button type="submit" size="sm" disabled={isProcessing || isRestoring || !inputPrompt.trim()}>
                <ArrowRight data-icon="inline-start" />Send request
              </Button>
            </InputGroupAddon>
          </InputGroup>
        </form>
      </Card>
    </section>
  );
}
