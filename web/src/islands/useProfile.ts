/** The visitor's profile in an island: null until known (render the default profile's view). */

import { useEffect, useState } from "preact/hooks";
import { type ProfileState, watchProfile } from "../logic/profileClient";

export function useProfile(): ProfileState | null {
  const [state, setState] = useState<ProfileState | null>(null);
  useEffect(() => watchProfile(setState), []);
  return state;
}
