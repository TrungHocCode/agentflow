import { useState } from 'react';
import ConfirmationBanner from './ConfirmationBanner';
import WatchlistDetail from './WatchlistDetail';
import WatchlistList from './WatchlistList';
import WatchlistSetup from './WatchlistSetup';

export default function Intelligence({ onOpenRun }) {
  const [view, setView] = useState({ name: 'list', watchlistId: null });
  const [notice, setNotice] = useState('');

  const banner = notice ? (
    <ConfirmationBanner message={notice} onDismiss={() => setNotice('')} />
  ) : null;

  if (view.name === 'setup') {
    return (
      <div className="page-frame">
      {banner}
      <WatchlistSetup
        onCreated={(watchlistId) => {
          setNotice('Watchlist created.');
          setView({ name: 'detail', watchlistId });
        }}
        onCancel={() => setView({ name: 'list', watchlistId: null })}
      />
      </div>
    );
  }
  if (view.name === 'detail') {
    return (
      <div className="page-frame">
      {banner}
      <WatchlistDetail
        watchlistId={view.watchlistId}
        onBack={() => setView({ name: 'list', watchlistId: null })}
        onOpenRun={onOpenRun}
      />
      </div>
    );
  }
  return (
    <div className="page-frame">
    {banner}
    <WatchlistList
      onSelect={(watchlistId) => setView({ name: 'detail', watchlistId })}
      onCreate={() => setView({ name: 'setup', watchlistId: null })}
    />
    </div>
  );
}
