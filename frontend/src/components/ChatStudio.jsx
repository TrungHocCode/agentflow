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
  queued: { label: 'Đang chờ', tone: 'pending' },
  running: { label: 'Đang thực hiện', tone: 'running' },
  completed: { label: 'Hoàn tất', tone: 'done' },
  failed: { label: 'Có lỗi', tone: 'failed' },
  interrupted: { label: 'Bị gián đoạn', tone: 'partial' },
  cancelled: { label: 'Đã hủy', tone: 'skipped' },
  abandoned: { label: 'Đã dừng', tone: 'skipped' }
};

const PROMPT_SUGGESTIONS = [
  'Tìm các tin nổi bật trên Hacker News, tóm tắt và tạo báo cáo Markdown.',
  'So sánh các mô hình LLM theo benchmark, ưu tiên nguồn chính thức.',
  'Nghiên cứu một chủ đề công nghệ và trình bày phát hiện bằng bảng, biểu đồ.'
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
          <h1 id="studio-title" className="page-title">Nghiên cứu bắt đầu từ câu hỏi của bạn</h1>
          <p className="page-description">Mô tả điều bạn muốn tìm hiểu. AgentFlow sẽ đề xuất các bước để bạn xem lại trước khi chạy.</p>
        </div>
        {conversationId && (
          <Button
            type="button"
            variant="outline"
            size="sm"
            onClick={onDeleteConversation}
            disabled={isDeletingConversation || isRestoring || (isProcessing && !isStreaming)}
            title={isRestoring ? 'Đang khôi phục hội thoại' : 'Xóa hội thoại'}
          >
            <Trash2 data-icon="inline-start" />
            <span className="delete-conversation-label">{isDeletingConversation ? 'Đang xóa…' : 'Xóa hội thoại'}</span>
          </Button>
        )}
      </div>

      <Card className="chat-workspace">
        <header className="chat-workspace__header">
          <div className="chat-workspace__identity">
            <div className="chat-assistant-mark" aria-hidden="true"><Sparkles /></div>
            <div>
              <p className="chat-workspace__title">Trợ lý nghiên cứu</p>
              <p className="chat-workspace__subtitle">Tìm kiếm · tổng hợp · báo cáo</p>
            </div>
          </div>
          {status && (
            <Badge variant={status.tone === 'failed' ? 'destructive' : 'secondary'} className={`run-status run-status--${status.tone}`}>
              {status.tone === 'running' ? <Spinner data-icon="inline-start" /> : null}
              {status.label}
            </Badge>
          )}
        </header>

        <div className="chat-thread" role="log" aria-label="Hội thoại nghiên cứu" aria-live="polite" aria-busy={isProcessing}>
          {isRestoring && messages.length === 0 ? (
            <Marker role="status" className="chat-status-row">
              <MarkerIcon><Spinner /></MarkerIcon>
              <MarkerContent>Đang khôi phục hội thoại và kiểm tra tiến độ…</MarkerContent>
            </Marker>
          ) : messages.length === 0 ? (
            <div className="chat-empty">
              <div className="chat-empty__mark" aria-hidden="true"><MessageSquareText /></div>
              <h2>Hôm nay bạn muốn khám phá điều gì?</h2>
              <p>
                Bắt đầu bằng một câu hỏi hoặc mục tiêu nghiên cứu. Bạn sẽ xem được kế hoạch trước khi cho phép các agent thực hiện.
              </p>
              <div className="prompt-suggestions" aria-label="Gợi ý câu hỏi">
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
                          <span>{isUser ? 'Bạn' : 'Trợ lý nghiên cứu'}</span>
                          {message.isGenerating && activeTurnId && (
                            <Button
                              type="button"
                              variant="ghost"
                              size="xs"
                              onClick={onCancelTurn}
                              disabled={isCancelling}
                            >
                              <XCircle data-icon="inline-start" />{isCancelling ? 'Đang hủy…' : 'Hủy'}
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
                                <MarkerContent>Đang chuẩn bị phản hồi…</MarkerContent>
                              </Marker>
                            ) : null}
                            {isError && (
                              <p className="chat-error-reference">
                                <AlertCircle />{message.errorId ? `Mã tham chiếu: ${message.errorId}` : 'Phản hồi chưa hoàn tất'}
                              </p>
                            )}
                          </BubbleContent>
                        </Bubble>
                        {message.duration && (
                          <MessageFooter className="chat-message__meta">
                            <Clock3 aria-hidden="true" />Thời gian phản hồi: {message.duration}s
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
                {isRestoring ? 'Đang khôi phục quy trình' : 'Đang phân tích yêu cầu'} · {liveThinkingSeconds}s
              </MarkerContent>
              {activeTurnId && (
                <Button type="button" variant="ghost" size="sm" onClick={onCancelTurn} disabled={isCancelling}>
                  <XCircle data-icon="inline-start" />{isCancelling ? 'Đang hủy…' : 'Hủy'}
                </Button>
              )}
            </Marker>
          )}

          {isStreaming && !runStatus && (
            <Marker role="status" className="chat-status-row">
              <MarkerIcon><Spinner /></MarkerIcon>
              <MarkerContent>Đang thực hiện các bước nghiên cứu · {liveThinkingSeconds}s</MarkerContent>
            </Marker>
          )}

          {activePlan?.length > 0 && (
            <Card className="chat-plan-card">
              <CardHeader>
                <div className="chat-plan-heading">
                  <span className="chat-plan-heading__icon" aria-hidden="true"><GitMerge /></span>
                  <div>
                    <CardTitle>{runStatus ? 'Quy trình nghiên cứu' : 'Kế hoạch đề xuất'}</CardTitle>
                    <CardDescription>
                      {runStatus ? 'Theo dõi tiến độ của từng bước ngay tại đây.' : 'Xem lại các bước trước khi bắt đầu.'}
                    </CardDescription>
                  </div>
                </div>
                <Badge variant={status?.tone === 'failed' ? 'destructive' : 'secondary'} className={status ? `run-status run-status--${status.tone}` : 'run-status run-status--pending'}>
                  {status?.label || 'Chờ bạn duyệt'}
                </Badge>
              </CardHeader>

              <CardContent>
                <WorkflowProgressGraph tasks={activePlan} runStatus={runStatus} />
                {runStatus === 'completed' && (
                  <Attachment state="done" size="sm" className="chat-attachment">
                    <AttachmentMedia><FileText /></AttachmentMedia>
                    <AttachmentContent>
                      <AttachmentTitle>Kết quả nghiên cứu đã sẵn sàng</AttachmentTitle>
                      <AttachmentDescription>Mở báo cáo và tải các tệp đầu ra</AttachmentDescription>
                    </AttachmentContent>
                    <AttachmentActions>
                      <AttachmentAction type="button" aria-label="Mở kết quả nghiên cứu" title="Mở kết quả" onClick={onOpenResults}>
                        <ArrowUpRight />
                      </AttachmentAction>
                    </AttachmentActions>
                  </Attachment>
                )}
              </CardContent>

              <CardFooter className="chat-plan-actions">
                {!runStatus ? (
                  <Button type="button" onClick={onApprovePlan} disabled={isProcessing}>
                    <Play data-icon="inline-start" />Duyệt và bắt đầu
                  </Button>
                ) : (
                  <>
                    <Button type="button" variant="outline" onClick={onOpenResults}>
                      <FileText data-icon="inline-start" />{runStatus === 'completed' ? 'Mở kết quả và tải tệp' : 'Xem kết quả'}
                    </Button>
                    {runId && (
                      <Button type="button" variant="outline" onClick={onViewRunDetails}>
                        <ArrowDownRight data-icon="inline-start" />Xem tiến độ chi tiết
                      </Button>
                    )}
                    {['queued', 'running'].includes(runStatus) && (
                      <Button type="button" variant="destructive" onClick={onCancelRun} disabled={isCancelling}>
                        <XCircle data-icon="inline-start" />{isCancelling ? 'Đang hủy…' : 'Hủy quy trình'}
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
              placeholder="Bạn muốn tìm hiểu hoặc tổng hợp điều gì?"
              aria-label="Yêu cầu nghiên cứu"
              disabled={isProcessing || isRestoring}
              rows={2}
            />
            <InputGroupAddon align="block-end" className="chat-composer__footer">
              <span className="chat-composer__hint">Enter để gửi · Shift + Enter để xuống dòng</span>
              <Button type="submit" size="sm" disabled={isProcessing || isRestoring || !inputPrompt.trim()}>
                <ArrowRight data-icon="inline-start" />Gửi yêu cầu
              </Button>
            </InputGroupAddon>
          </InputGroup>
        </form>
      </Card>
    </section>
  );
}
