import { useState } from 'react';
import './VideoThumbnail.css';

export function VideoThumbnail({ gameId, title, className = '' }: { gameId?: string; title: string; className?: string }) {
  const [failedSource, setFailedSource] = useState<string | null>(null);
  const source = gameId ? `/api/games/${encodeURIComponent(gameId)}/thumbnail` : null;
  return <span className={`video-thumbnail ${className}`} aria-label={`Video preview: ${title}`}>
    {source && source !== failedSource
      ? <img src={source} alt="" loading="lazy" decoding="async" onError={() => setFailedSource(source)} />
      : <span className="video-thumbnail-placeholder" aria-hidden="true"><svg viewBox="0 0 24 24" fill="none"><rect x="3" y="5" width="18" height="14" rx="3" stroke="currentColor"/><path d="m10 9 5 3-5 3z" fill="currentColor"/></svg></span>}
    <span className="video-thumbnail-play" aria-hidden="true">▶</span>
  </span>;
}
