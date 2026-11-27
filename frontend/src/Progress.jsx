import React, {useEffect, useState} from 'react';

export function Elapsed({started}) {
  const [now, setNow] = useState(Date.now);
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, []);
  const seconds = Math.max(0, Math.floor((now - new Date(started).getTime()) / 1000));
  return <time className="elapsed">{Math.floor(seconds / 60)}:{String(seconds % 60).padStart(2, '0')}</time>;
}

export default function Progress({label}) {
  return <div className="indeterminate-progress" role="progressbar" aria-label={label}><span/></div>;
}
