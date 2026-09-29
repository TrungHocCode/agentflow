import { useMemo, useState } from 'react';
import { Code2, Database, FileText, Globe2, Layers3, Mail, Search, Wrench } from 'lucide-react';
import { Badge } from './ui/badge';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from './ui/card';
import { InputGroup, InputGroupAddon, InputGroupInput, InputGroupText } from './ui/input-group';
import { Tabs, TabsContent, TabsList, TabsTrigger } from './ui/tabs';

function toolIcon(name) {
  if (name.includes('crawler') || name.includes('http') || name.includes('search')) return Globe2;
  if (name.includes('python')) return Code2;
  if (name.includes('report') || name.includes('file')) return FileText;
  if (name.includes('database')) return Database;
  if (name.includes('email')) return Mail;
  return Wrench;
}

function displayName(identifier) {
  return String(identifier || '')
    .replace(/[_-]+/g, ' ')
    .replace(/\b\w/g, character => character.toUpperCase());
}

export default function CatalogBrowser({ tools, agents }) {
  const [searchTerm, setSearchTerm] = useState('');
  const normalizedSearch = searchTerm.trim().toLowerCase();
  const filteredTools = useMemo(() => tools.filter(tool => [
    tool.name,
    tool.description,
    tool.unavailable_reason
  ].some(value => String(value || '').toLowerCase().includes(normalizedSearch))), [tools, normalizedSearch]);
  const filteredAgents = useMemo(() => agents.filter(agent => [
    agent.name,
    ...(agent.available_tool_names || agent.tool_names || []),
    ...(agent.blocked_tool_names || [])
  ].some(value => String(value || '').toLowerCase().includes(normalizedSearch))), [agents, normalizedSearch]);

  return (
    <section className="page-frame" aria-labelledby="catalog-title">
      <div className="page-heading">
        <div className="page-heading__copy">
          <p className="page-eyebrow">NĂNG LỰC NGHIÊN CỨU</p>
          <h1 id="catalog-title" className="page-title">Công cụ & trợ lý</h1>
          <p className="page-description">Các khả năng mà agent có thể dùng để tìm kiếm, phân tích và tạo đầu ra.</p>
        </div>
      </div>

      <Card className="surface-card">
        <CardHeader className="catalog-toolbar">
          <div>
            <CardTitle>Danh mục năng lực</CardTitle>
            <CardDescription>{tools.length} công cụ · {agents.length} trợ lý</CardDescription>
          </div>
          <InputGroup className="catalog-search">
            <InputGroupAddon align="inline-start">
              <InputGroupText><Search /></InputGroupText>
            </InputGroupAddon>
            <InputGroupInput
              type="search"
              aria-label="Tìm công cụ hoặc trợ lý"
              placeholder="Tìm theo tên hoặc mô tả…"
              value={searchTerm}
              onChange={event => setSearchTerm(event.target.value)}
            />
          </InputGroup>
        </CardHeader>
        <CardContent>
          <Tabs defaultValue="tools">
            <TabsList aria-label="Loại năng lực">
              <TabsTrigger value="tools">Công cụ <span className="catalog-tab-count">{tools.length}</span></TabsTrigger>
              <TabsTrigger value="agents">Trợ lý <span className="catalog-tab-count">{agents.length}</span></TabsTrigger>
            </TabsList>

            <TabsContent value="tools" className="catalog-tab-content">
              {filteredTools.length === 0 ? (
                <p className="catalog-empty">Không tìm thấy công cụ phù hợp.</p>
              ) : (
                <div className="catalog-grid">
                  {filteredTools.map((tool) => {
                    const Icon = toolIcon(tool.name);
                    const isAvailable = tool.is_available !== false;
                    return (
                      <Card key={tool.name} className="catalog-item">
                        <CardHeader>
                          <div className="catalog-item__top">
                            <div className="flex min-w-0 items-center gap-2.5">
                              <span className="catalog-item__icon" aria-hidden="true"><Icon /></span>
                              <CardTitle className="catalog-item__title" title={tool.name}>{displayName(tool.name)}</CardTitle>
                            </div>
                            <Badge variant={isAvailable ? 'secondary' : 'destructive'} className={isAvailable ? 'run-status run-status--done' : undefined} title={tool.unavailable_reason || undefined}>
                              {isAvailable ? 'Sẵn sàng' : 'Tạm tắt'}
                            </Badge>
                          </div>
                        </CardHeader>
                        <CardContent className="flex flex-1 flex-col gap-2">
                          <CardDescription className="catalog-item__description">{tool.description || 'Chưa có mô tả.'}</CardDescription>
                          {!isAvailable && tool.unavailable_reason && (
                            <p className="catalog-unavailable">{tool.unavailable_reason}</p>
                          )}
                        </CardContent>
                      </Card>
                    );
                  })}
                </div>
              )}
            </TabsContent>

            <TabsContent value="agents" className="catalog-tab-content">
              {filteredAgents.length === 0 ? (
                <p className="catalog-empty">Không tìm thấy trợ lý phù hợp.</p>
              ) : (
                <div className="catalog-grid">
                  {filteredAgents.map((agent) => {
                    const availableToolNames = agent.available_tool_names || agent.tool_names || [];
                    const blockedToolNames = agent.blocked_tool_names || [];
                    return (
                      <Card key={agent.name} className="catalog-item">
                        <CardHeader>
                          <div className="catalog-item__top">
                            <div className="flex min-w-0 items-center gap-2.5">
                              <span className="catalog-item__icon" aria-hidden="true"><Layers3 /></span>
                              <CardTitle className="catalog-item__title" title={agent.name}>{displayName(agent.name)}</CardTitle>
                            </div>
                            <Badge variant="secondary" className="run-status run-status--done">Sẵn sàng</Badge>
                          </div>
                          <CardDescription>{availableToolNames.length} công cụ được cấp quyền</CardDescription>
                        </CardHeader>
                        <CardContent className="catalog-tool-list">
                          {availableToolNames.map((toolName) => (
                            <Badge key={toolName} variant="outline" className="catalog-tool-chip">{toolName}</Badge>
                          ))}
                          {blockedToolNames.map((toolName) => (
                            <Badge key={`blocked-${toolName}`} variant="destructive" title="Bị tắt theo chính sách triển khai">
                              {toolName} · tạm tắt
                            </Badge>
                          ))}
                        </CardContent>
                      </Card>
                    );
                  })}
                </div>
              )}
            </TabsContent>
          </Tabs>
        </CardContent>
      </Card>
    </section>
  );
}
