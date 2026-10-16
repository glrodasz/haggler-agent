/**
 * Searchable country field (WAI-ARIA combobox with a list popup): type to filter by the Spanish
 * name ignoring accents, pick with the arrow keys and Enter or by tapping. Focus never leaves
 * the input; the highlighted option is exposed through aria-activedescendant. Escape or leaving
 * the field without picking restores the current country, so the profile never holds a
 * half-typed value.
 */

import type { TargetedKeyboardEvent } from "preact";
import { useEffect, useMemo, useRef, useState } from "preact/hooks";
import { es } from "../i18n/es";
import { type SearchableCountry, searchCountries } from "../logic/countrySearch";
import { flagEmoji } from "../logic/format";

const t = es.compare;

export function CountryCombobox(props: {
  id: string;
  countries: readonly SearchableCountry[];
  /** Current country, or null for an "add" field that empties itself after each pick. */
  value: string | null;
  onSelect: (cc: string) => void;
  placeholder?: string;
  describedBy?: string;
}) {
  const { id, countries, value, onSelect } = props;
  const listId = `${id}-lista`;
  const statusId = `${id}-estado`;
  const selected = useMemo(() => countries.find((c) => c.cc === value), [countries, value]);
  const [text, setText] = useState(selected?.name ?? "");
  const [open, setOpen] = useState(false);
  /** True once the visitor has typed: until then the whole list shows, not just the match. */
  const [dirty, setDirty] = useState(false);
  const [active, setActive] = useState(-1);
  const list = useRef<HTMLUListElement>(null);

  const matches = useMemo(
    () => (dirty ? searchCountries(countries, text) : [...countries]),
    [countries, text, dirty],
  );

  useEffect(() => {
    if (!open) setText(selected?.name ?? "");
  }, [selected, open]);

  const highlighted = open ? matches[active] : undefined;

  useEffect(() => {
    if (!highlighted) return;
    list.current?.querySelector(`#${CSS.escape(optionId(highlighted.cc))}`)?.scrollIntoView({
      block: "nearest",
    });
  }, [highlighted]);

  function optionId(cc: string) {
    return `${id}-op-${cc}`;
  }

  function show() {
    if (open) return;
    setOpen(true);
    setDirty(false);
    setActive(Math.max(0, countries.findIndex((c) => c.cc === value)));
  }

  function close() {
    setOpen(false);
    setDirty(false);
    setActive(-1);
    setText(selected?.name ?? "");
  }

  function choose(country: SearchableCountry) {
    setOpen(false);
    setDirty(false);
    setActive(-1);
    setText(value === null ? "" : country.name);
    if (country.cc !== value) onSelect(country.cc);
  }

  function onKeyDown(e: TargetedKeyboardEvent<HTMLInputElement>) {
    const last = matches.length - 1;
    switch (e.key) {
      case "ArrowDown":
      case "ArrowUp": {
        e.preventDefault();
        if (!open || e.altKey) {
          show();
          return;
        }
        if (last < 0) return;
        const step = e.key === "ArrowDown" ? 1 : -1;
        setActive((i) => (i < 0 ? (step > 0 ? 0 : last) : (i + step + last + 1) % (last + 1)));
        return;
      }
      case "Enter":
        if (highlighted) {
          e.preventDefault();
          choose(highlighted);
        }
        return;
      case "Escape":
        if (open) {
          e.preventDefault();
          close();
        }
        return;
    }
  }

  const expanded = open && matches.length > 0;
  const showFlag = !dirty && selected !== undefined && text === selected.name;

  return (
    <div class={`combobox ${showFlag ? "has-flag" : ""}`}>
      {showFlag && (
        <span class="combobox-flag" aria-hidden="true">
          {flagEmoji(selected.cc)}
        </span>
      )}
      <input
        id={id}
        type="text"
        role="combobox"
        autocomplete="off"
        autocapitalize="off"
        spellcheck={false}
        aria-autocomplete="list"
        aria-expanded={expanded}
        aria-controls={listId}
        aria-activedescendant={highlighted ? optionId(highlighted.cc) : undefined}
        aria-describedby={[props.describedBy, statusId].filter(Boolean).join(" ")}
        placeholder={props.placeholder}
        value={text}
        onFocus={(e) => e.currentTarget.select()}
        onClick={show}
        onInput={(e) => {
          setText(e.currentTarget.value);
          setDirty(true);
          setOpen(true);
          setActive(0);
        }}
        onKeyDown={onKeyDown}
        onBlur={close}
      />
      <ul id={listId} ref={list} role="listbox" class="combobox-list" hidden={!expanded}>
        {matches.map((c, i) => (
          <li
            key={c.cc}
            id={optionId(c.cc)}
            role="option"
            aria-selected={i === active}
            class={`combobox-option ${c.cc === value ? "is-current" : ""}`}
            onMouseDown={(e) => e.preventDefault()}
            onMouseMove={() => i !== active && setActive(i)}
            onClick={() => choose(c)}
          >
            <span class="flag" aria-hidden="true">
              {flagEmoji(c.cc)}
            </span>
            {c.name}
          </li>
        ))}
      </ul>
      {open && matches.length === 0 && (
        <p class="combobox-empty" aria-hidden="true">
          {t.countryNoMatch}
        </p>
      )}
      <span id={statusId} class="visually-hidden" aria-live="polite">
        {open && dirty ? t.countryMatches(matches.length) : ""}
      </span>
    </div>
  );
}
