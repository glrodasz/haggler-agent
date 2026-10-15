/**
 * Segmented slider for the accepted risk: one pill per level with the saving it unlocks (as a
 * share, so long amounts never stretch the strip). Click a level, drag the thumb (it sticks to
 * each section and jumps to the next one) or use the arrow keys.
 */

import type { TargetedKeyboardEvent, TargetedPointerEvent } from "preact";
import { useRef, useState } from "preact/hooks";
import { es } from "../i18n/es";
import { formatPercent } from "../logic/format";

/** How far the thumb follows the pointer inside a section before it jumps (0 = rigid). */
const STICKINESS = 0.3;

export function RiskSlider<L extends number>(props: {
  levels: readonly L[];
  value: L;
  onChange: (level: L) => void;
  /** Saving per level as a percentage (7.5 = 7,5 %). */
  pct: Record<L, number>;
  names: Record<number, string | undefined>;
  labelledBy: string;
}) {
  const { levels, value, onChange, pct, names } = props;
  const track = useRef<HTMLDivElement>(null);
  const [dragPos, setDragPos] = useState<number | null>(null);
  const last = levels.length - 1;
  const index = Math.max(0, levels.indexOf(value));

  const select = (i: number) => {
    const level = levels[Math.min(last, Math.max(0, i))];
    if (level !== undefined && level !== value) onChange(level);
  };

  // Continuous slot position under the pointer: 0 at the centre of the first level.
  const slotAt = (clientX: number): number => {
    const rect = track.current?.getBoundingClientRect();
    if (!rect || rect.width === 0) return index;
    const x = ((clientX - rect.left) / rect.width) * levels.length - 0.5;
    return Math.min(last, Math.max(0, x));
  };

  const onPointerDown = (e: TargetedPointerEvent<HTMLDivElement>) => {
    if (e.button !== 0) return;
    e.currentTarget.setPointerCapture(e.pointerId);
    e.currentTarget.focus();
    const slot = slotAt(e.clientX);
    setDragPos(slot);
    select(Math.round(slot));
  };

  const onPointerMove = (e: TargetedPointerEvent<HTMLDivElement>) => {
    if (dragPos === null) return;
    const slot = slotAt(e.clientX);
    setDragPos(slot);
    select(Math.round(slot));
  };

  const endDrag = () => setDragPos(null);

  const onKeyDown = (e: TargetedKeyboardEvent<HTMLDivElement>) => {
    const next =
      e.key === "ArrowRight" || e.key === "ArrowUp"
        ? index + 1
        : e.key === "ArrowLeft" || e.key === "ArrowDown"
          ? index - 1
          : e.key === "Home"
            ? 0
            : e.key === "End"
              ? last
              : null;
    if (next === null) return;
    e.preventDefault();
    select(next);
  };

  const dragging = dragPos !== null;
  const thumbAt = dragging ? index + (dragPos - index) * STICKINESS : index;
  const valueText = `${es.riskLevel(value)}${names[value] ? `, ${names[value]}` : ""}: ${es.compare.riskSliderSaving(formatPercent(pct[value] ?? 0, { digits: 0 }))}`;

  return (
    <div
      class="risk-slider"
      role="slider"
      tabIndex={0}
      aria-labelledby={props.labelledBy}
      aria-valuemin={levels[0]}
      aria-valuemax={levels[last]}
      aria-valuenow={value}
      aria-valuetext={valueText}
      data-dragging={dragging ? "" : undefined}
      onPointerDown={onPointerDown}
      onPointerMove={onPointerMove}
      onPointerUp={endDrag}
      onPointerCancel={endDrag}
      onLostPointerCapture={endDrag}
      onKeyDown={onKeyDown}
    >
      <div class="risk-slider-track" ref={track} style={{ "--levels": levels.length }}>
        <span
          class="risk-slider-thumb"
          aria-hidden="true"
          style={{ transform: `translateX(${thumbAt * 100}%)` }}
        />
        {levels.map((k) => (
          <span
            key={k}
            class="risk-slider-stop"
            data-active={k === value ? "" : undefined}
            title={names[k]}
            aria-hidden="true"
          >
            <span class={`risk-dot-label risk-${k}`}>{es.riskLevel(k)}</span>
            <span class="amount">{formatPercent(pct[k] ?? 0, { digits: 0 })}</span>
          </span>
        ))}
      </div>
    </div>
  );
}
