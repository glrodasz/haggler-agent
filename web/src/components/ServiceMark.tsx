import {
  EVEN_ODD_ICONS,
  SERVICE_ICON_PATHS,
  SERVICE_WORDMARKS,
  WIDE_ICON_VIEWBOX,
} from "../logic/serviceIcons";

/** A service's brand mark in a small tile, or its wordmark or initial when there is no mark. Decorative. */
export function ServiceMark({ id, name }: { id: string; name: string }) {
  const path = SERVICE_ICON_PATHS[id];
  const wordmark = SERVICE_WORDMARKS[id];
  const wide = WIDE_ICON_VIEWBOX[id];
  return (
    <span class={`monogram${wordmark && !path ? " is-wordmark" : ""}`} aria-hidden="true">
      {path ? (
        <svg
          viewBox={wide ?? "0 0 24 24"}
          width={wide ? 22 : 16}
          height={16}
          fill="currentColor"
          focusable="false"
        >
          <path d={path} fill-rule={EVEN_ODD_ICONS.has(id) ? "evenodd" : undefined} />
        </svg>
      ) : (
        (wordmark ?? name.charAt(0).toUpperCase())
      )}
    </span>
  );
}
