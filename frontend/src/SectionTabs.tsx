import { useRef } from 'react';

/** Arrow-key navigation follows the WAI-ARIA automatic-activation tab pattern. */
export function SectionTabs<T extends string>({ id, label, tabs, value, onChange }: {
  id: string; label: string; tabs: readonly { id: T; label: string }[];
  value: T; onChange: (value: T) => void;
}) {
  const buttons = useRef<(HTMLButtonElement | null)[]>([]);
  return <div className="section-tabs" role="tablist" aria-label={label}>
    {tabs.map((tab, index) => <button key={tab.id} ref={node => { buttons.current[index] = node; }}
      type="button" role="tab" id={`${id}-tab-${tab.id}`} aria-controls={`${id}-panel-${tab.id}`}
      aria-selected={value === tab.id} tabIndex={value === tab.id ? 0 : -1}
      onClick={() => onChange(tab.id)} onKeyDown={event => {
        const next = event.key === 'ArrowRight' ? (index + 1) % tabs.length
          : event.key === 'ArrowLeft' ? (index + tabs.length - 1) % tabs.length
          : event.key === 'Home' ? 0 : event.key === 'End' ? tabs.length - 1 : null;
        if (next === null) return;
        event.preventDefault(); onChange(tabs[next].id); buttons.current[next]?.focus();
      }}>{tab.label}</button>)}
  </div>;
}
