import {useEffect, useRef, useState} from 'react';
import {Handle, Position} from '@xyflow/react';

export const Tag = ({tone = '', children}) => <span className={'pf-tag ' + tone}>{children}</span>;

export function Seg({value, onChange, options, label}) {
  return <div className="pf-seg" role="group" aria-label={label}>{options.map(([key, text]) =>
    <button type="button" key={key} aria-pressed={value === key} className={value === key ? 'on' : ''} onClick={() => onChange(key)}>{text}</button>)}</div>;
}

export const Loading = ({text}) => <p className="pf-empty-line" role="status">{text}</p>;
export const Problem = ({text}) => <p className="pf-error" role="alert">{text}</p>;

export function Empty({title, text, href, action}) {
  return <section className="pf-empty"><h2>{title}</h2><p>{text}</p>{href && <a className="button secondary" href={href}>{action}</a>}</section>;
}

// Seconds since `since`, ticking while mounted.
export function Elapsed({since}) {
  const [now, setNow] = useState(Date.now());
  useEffect(() => { const timer = setInterval(() => setNow(Date.now()), 1000); return () => clearInterval(timer); }, []);
  return <span className="pf-elapsed">{Math.max(0, Math.round((now - since) / 1000))} s</span>;
}

// Generic node: title, meta line, optional field list. One look for every canvas.
export function PfNode({data, selected}) {
  return <div className={'pf-node k-' + (data.kind || 'plain') + (selected ? ' selected' : '')} data-node-kind={data.kind}>
    <Handle type="target" position={data.reverse ? Position.Right : data.vertical ? Position.Top : Position.Left} />
    <div className="pf-node-title">{data.title}</div>
    {data.meta && <div className="pf-node-meta">{data.meta}</div>}
    <Handle type="source" position={data.reverse ? Position.Left : data.vertical ? Position.Bottom : Position.Right} />
  </div>;
}

// A modal confirm step (native <dialog>): what will happen is shown before any write. Esc or Cancel closes it.
export function Confirm({title, children, actions, onClose}) {
  const ref = useRef(null);
  useEffect(() => { if (ref.current && !ref.current.open) ref.current.showModal(); }, []);
  return <dialog ref={ref} className="pf-dialog" aria-label={title} onClose={onClose}>
    <h2>{title}</h2>{children}<div className="pf-actions">{actions}</div></dialog>;
}
