import { useState } from 'react';
import WatchlistDetail from './WatchlistDetail';
import WatchlistList from './WatchlistList';
import WatchlistSetup from './WatchlistSetup';

export default function Intelligence() {
  const [view, setView] = useState({ name: 'list', watchlistId: null });

  if (view.name === 'setup') {
    return (
      <WatchlistSetup onCreated={(watchlistId) => setView({ name: 'detail', watchlistId })} onCancel={() => setView({ name: 'list', watchlistId: null })} />
    );
  }
  if (view.name === 'detail') {
    return (
      <WatchlistDetail
        watchlistId={view.watchlistId}
        onBack={() => setView({ name: 'list', watchlistId: null })}
      />
    );
  }
  return (
    <WatchlistList
      onSelect={(watchlistId) => setView({ name: 'detail', watchlistId })}
      onCreate={() => setView({ name: 'setup', watchlistId: null })}
    />
  );
}
