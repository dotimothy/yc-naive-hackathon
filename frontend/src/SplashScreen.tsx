import { useEffect, useRef, useState, type ReactNode } from 'react';
import './SplashScreen.css';

/** A short brand introduction; the app loads underneath it. No media or job is started. */
export function SplashGate({ children }: { children: ReactNode }) {
  const [visible, setVisible] = useState(true);
  return <><div className="pl-entry-content" inert={visible} aria-hidden={visible || undefined}>{children}</div>{visible && <SplashScreen onComplete={() => setVisible(false)} />}</>;
}

function SplashScreen({ onComplete }: { onComplete: () => void }) {
  const skip = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    const finish = () => {
      const restoreFocus = document.activeElement === skip.current;
      onComplete();
      if (restoreFocus) requestAnimationFrame(() => document.querySelector<HTMLElement>('[role="tab"][aria-selected="true"]')?.focus({ preventScroll: true }));
    };
    const reduced = window.matchMedia('(prefers-reduced-motion: reduce)');
    const timer = window.setTimeout(finish, reduced.matches ? 450 : 3400);
    const key = (e: KeyboardEvent) => { if (e.key === 'Escape') finish(); if (e.key === 'Tab') { e.preventDefault(); skip.current?.focus(); } };
    const motionChanged = () => { if (reduced.matches) finish(); };
    window.addEventListener('keydown', key);
    reduced.addEventListener('change', motionChanged);
    skip.current?.focus({ preventScroll: true });
    return () => { window.clearTimeout(timer); window.removeEventListener('keydown', key); reduced.removeEventListener('change', motionChanged); document.body.style.overflow = previousOverflow; };
  }, [onComplete]);

  return <div className="pl-splash" role="dialog" aria-modal="true" aria-label="Welcome to PlayLattice">
    <button ref={skip} className="pl-splash-skip" onClick={() => { onComplete(); requestAnimationFrame(() => document.querySelector<HTMLElement>('[role="tab"][aria-selected="true"]')?.focus({ preventScroll: true })); }}>Skip Intro <span aria-hidden="true">↗</span></button>
    <div className="pl-splash-inner">
      <div className="pl-splash-brand">
        <img src="/favicon.png" width="132" height="132" alt="" />
        <span className="pl-splash-eyebrow">Every Play Has A Story</span>
        <h1>Play<span>Lattice</span></h1>
        <p>See the play. Find the moment.</p>
      </div>
      <div className="pl-splash-sequence" aria-hidden="true">
        <svg className="pl-splash-film" viewBox="0 0 620 250" fill="none">
          <defs><linearGradient id="pl-court-glow" x1="100" y1="30" x2="520" y2="190" gradientUnits="userSpaceOnUse"><stop stopColor="#183d4b"/><stop offset="1" stopColor="#101a24"/></linearGradient></defs>
          <g className="pl-splash-source">
            <rect x="100" y="22" width="420" height="168" rx="10" fill="url(#pl-court-glow)" stroke="#355462"/>
            <path d="M122 40H498V174H122zM310 40V174M122 70H168V144H122M498 70H452V144H498" stroke="#608080" strokeOpacity=".6"/>
            <circle cx="310" cy="107" r="34" stroke="#608080" strokeOpacity=".6"/>
            <path d="M168 70A48 48 0 0 1 168 144M452 70A48 48 0 0 0 452 144" stroke="#608080" strokeOpacity=".6"/>
            <g fill="#8bd5d9"><circle cx="248" cy="90" r="7"/><circle cx="365" cy="146" r="7"/><circle cx="402" cy="76" r="7"/></g>
            <g fill="#ff9758"><circle cx="287" cy="116" r="7"/><circle cx="374" cy="103" r="7"/><circle cx="225" cy="143" r="7"/></g>
            <rect className="pl-splash-track" x="272" y="97" width="30" height="38" rx="4" stroke="#b8f56b" strokeWidth="2"/>
            <path d="M287 116Q326 151 374 103" stroke="#ff9758" strokeWidth="2" strokeDasharray="4 6"/>
            <rect x="114" y="34" width="49" height="18" rx="3" fill="#080d13"/>
            <path d="m130 39 7 4-7 4z" fill="#ff9758"/><path d="M143 41H153M143 45H150" stroke="#9fb1c5"/>
          </g>
          <g className="pl-splash-timeline">
            <rect x="100" y="207" width="420" height="29" rx="4" fill="#111c28" stroke="#2f3d50"/>
            {[0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11].map(i => <rect key={i} x={105 + i * 34} y="212" width="29" height="19" rx="2" fill={i > 3 && i < 8 ? '#986044' : '#263947'} />)}
            <g className="pl-splash-selection"><rect x="240" y="205" width="134" height="33" rx="4" stroke="#ff8540" strokeWidth="2" fill="#ff6b1a" fillOpacity=".08"/><path d="M244 214V229M370 214V229" stroke="#ffb488" strokeWidth="3"/></g>
            <path className="pl-splash-cut" d="M307 194V246" stroke="#f4f6f9" strokeWidth="2"/>
          </g>
          <g className="pl-splash-export">
            <rect x="455" y="55" width="90" height="154" rx="10" fill="#111f29" stroke="#ff9758" strokeWidth="2"/>
            <rect x="464" y="65" width="72" height="108" rx="4" fill="#20424b"/>
            <path d="M464 105H536M500 65V173" stroke="#669695" strokeOpacity=".6"/>
            <circle cx="500" cy="119" r="11" fill="#ff9758"/><rect x="485" y="104" width="30" height="38" rx="4" stroke="#b8f56b" strokeWidth="2"/>
            <path d="M468 187H523M468 195H510" stroke="#dce3ec" strokeWidth="3" strokeLinecap="round"/>
            <circle cx="540" cy="61" r="13" fill="#b8f56b"/><path d="m534 61 4 4 7-8" stroke="#152010" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"/>
          </g>
        </svg>
        <div className="pl-splash-ball"><svg viewBox="0 0 64 64" fill="none"><circle cx="32" cy="32" r="29" fill="#ff792b" stroke="#ffae70" strokeWidth="2"/><path d="M3 32H61M32 3V61M12 11C42 18 42 46 12 53M52 11C22 18 22 46 52 53" stroke="#542a1b" strokeWidth="2.2"/></svg></div>
      </div>
      <div className="pl-splash-caption" aria-hidden="true"><span>Play</span><i>→</i><span>Insight</span><i>→</i><span className="pl-splash-final-word">Highlight</span></div>
    </div>
    <span className="pl-splash-corner" aria-hidden="true">From Court To Clip</span>
  </div>;
}
