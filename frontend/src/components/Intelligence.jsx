import { useState } from 'react';
import WatchlistDetail from './WatchlistDetail';
import WatchlistList from './WatchlistList';
import WatchlistSetup from './WatchlistSetup';

export default function Intelligence() {
  const [view, setView] = useState({ name: 'list', watchlistId: null });

  if (view.name === 'setup') {
    return (
      <div className="page-frame">
      <WatchlistSetup onCreated={(watchlistId) => setView({ name: 'detail', watchlistId })} onCancel={() => setView({ name: 'list', watchlistId: null })} />
      </div>
    );
  }
  if (view.name === 'detail') {
    return (
      <div className="page-frame">
      <WatchlistDetail
        watchlistId={view.watchlistId}
        onBack={() => setView({ name: 'list', watchlistId: null })}
      />
      </div>
    );
  }
  return (
    <div className="page-frame">
    <WatchlistList
      onSelect={(watchlistId) => setView({ name: 'detail', watchlistId })}
      onCreate={() => setView({ name: 'setup', watchlistId: null })}
    />
    </div>
  );
}
