import FlowCanvas from './FlowCanvas';
import CatalogBrowser from './CatalogBrowser';

// Advanced surfaces only; the primary shell stays Watchlists/Changes/Briefs/Settings.
export default function SettingsView({ plan, tools, agents }) {
  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <FlowCanvas plan={plan} />
      <CatalogBrowser tools={tools} agents={agents} />
    </div>
  );
}
