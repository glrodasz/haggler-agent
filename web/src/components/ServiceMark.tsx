import { SERVICE_ICON_PATHS } from "../logic/serviceIcons";

/** A service's brand mark in a small tile, or its initial when there is no mark. Decorative. */
export function ServiceMark({ id, name }: { id: string; name: string }) {
  const path = SERVICE_ICON_PATHS[id];
  return (
    <span class="monogram" aria-hidden="true">
      {path ? (
        <svg viewBox="0 0 24 24" width="16" height="16" fill="currentColor" focusable="false">
          <path d={path} />
        </svg>
      ) : (
        name.charAt(0).toUpperCase()
      )}
    </span>
  );
}
