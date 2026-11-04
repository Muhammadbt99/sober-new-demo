import { useEffect, useId, useRef, useState, type KeyboardEvent } from "react";
import { LoaderCircle, LocateFixed, MapPin, Search, X } from "lucide-react";

import { searchPlaces, type Place } from "@/lib/api";

type PlaceSearchProps = {
  id: string;
  label: string;
  placeholder: string;
  value: Place | null;
  onSelect: (place: Place | null) => void;
};

/** A browser refusal in words a driver can act on. */
function locationProblem(error: GeolocationPositionError): string {
  if (error.code === error.PERMISSION_DENIED) {
    return "Location is blocked. Allow it for this site in the browser, or type a place.";
  }
  if (error.code === error.TIMEOUT) {
    return "Finding your location timed out. Try again or type a place.";
  }
  return "This device could not find its location. Type a place instead.";
}

export function PlaceSearch({ id, label, placeholder, value, onSelect }: PlaceSearchProps) {
  const [text, setText] = useState(value?.name ?? "");
  const [results, setResults] = useState<Place[]>([]);
  const [open, setOpen] = useState(false);
  const [queryActive, setQueryActive] = useState(false);
  const [active, setActive] = useState(0);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [locating, setLocating] = useState(false);
  const [locationError, setLocationError] = useState<string | null>(null);
  const wrapperRef = useRef<HTMLDivElement | null>(null);
  const listId = useId();

  useEffect(() => {
    // A scenario or swatch can replace the value without the user touching
    // this field. Do not turn that into a search or an open listbox.
    setText(value?.name ?? "");
    setResults([]);
    setOpen(false);
    setQueryActive(false);
    setError(null);
    setLoading(false);
  }, [value?.id, value?.name]);

  useEffect(() => {
    const query = text.trim();
    if (!queryActive) {
      setLoading(false);
      return;
    }
    if (query.length < 2) {
      setResults([]);
      setLoading(false);
      setError(null);
      return;
    }
    const controller = new AbortController();
    const timer = window.setTimeout(() => {
      setLoading(true);
      setError(null);
      searchPlaces(query, controller.signal)
        .then((places) => {
          setResults(places);
          setActive(0);
          setOpen(true);
        })
        .catch((caught: unknown) => {
          if (caught instanceof DOMException && caught.name === "AbortError") return;
          setResults([]);
          setError(caught instanceof Error ? caught.message : "Place search unavailable");
        })
        .finally(() => setLoading(false));
    }, 350);
    return () => {
      controller.abort();
      window.clearTimeout(timer);
    };
  }, [queryActive, text]);

  useEffect(() => {
    function onPointerDown(event: MouseEvent) {
      if (!wrapperRef.current?.contains(event.target as Node)) {
        setOpen(false);
        setQueryActive(false);
      }
    }
    document.addEventListener("mousedown", onPointerDown);
    return () => document.removeEventListener("mousedown", onPointerDown);
  }, []);

  function choose(place: Place) {
    setText(place.name);
    setOpen(false);
    setQueryActive(false);
    onSelect(place);
  }

  function locateMe() {
    setLocationError(null);
    if (!("geolocation" in navigator)) {
      setLocationError("This browser cannot share a location. Type a place instead.");
      return;
    }
    setLocating(true);
    navigator.geolocation.getCurrentPosition(
      ({ coords }) => {
        setLocating(false);
        choose({
          id: `current-${coords.latitude.toFixed(5)}-${coords.longitude.toFixed(5)}`,
          name: "Current location",
          detail: `${coords.latitude.toFixed(4)}, ${coords.longitude.toFixed(4)}`,
          lat: coords.latitude,
          lon: coords.longitude,
        });
      },
      (problem) => {
        setLocating(false);
        setLocationError(locationProblem(problem));
      },
      { enableHighAccuracy: false, maximumAge: 60_000, timeout: 10_000 },
    );
  }

  function onKeyDown(event: KeyboardEvent<HTMLInputElement>) {
    if (!open && (event.key === "ArrowDown" || event.key === "ArrowUp")) {
      setOpen(true);
      return;
    }
    if (event.key === "ArrowDown") {
      event.preventDefault();
      setActive((current) => Math.min(results.length - 1, current + 1));
    } else if (event.key === "ArrowUp") {
      event.preventDefault();
      setActive((current) => Math.max(0, current - 1));
    } else if (event.key === "Enter" && open && results[active]) {
      event.preventDefault();
      choose(results[active]!);
    } else if (event.key === "Escape") {
      setOpen(false);
    }
  }

  return (
    <div className="field place-search" ref={wrapperRef}>
      <label htmlFor={id}>{label}</label>
      <div className="place-input-wrap">
        <MapPin size={13} className="place-input-icon" aria-hidden="true" />
        <input
          id={id}
          className="input place-input"
          value={text}
          placeholder={placeholder}
          autoComplete="off"
          spellCheck={false}
          role="combobox"
          aria-expanded={open}
          aria-controls={listId}
          aria-autocomplete="list"
          aria-activedescendant={open && results[active] ? `${listId}-${active}` : undefined}
          onChange={(event) => {
            setText(event.target.value);
            setQueryActive(true);
            setOpen(true);
          }}
          onFocus={() => {
            setQueryActive(true);
            if (results.length > 0) setOpen(true);
          }}
          onKeyDown={onKeyDown}
        />
        {loading && (
          <LoaderCircle
            size={13}
            className="place-input-spinner animate-spin"
            aria-label="Searching"
          />
        )}
        <button
          type="button"
          className="place-locate"
          aria-label={`Use current location for ${label}`}
          title="Use my current location"
          disabled={locating}
          onClick={locateMe}
        >
          {locating ? (
            <LoaderCircle size={13} className="animate-spin" aria-hidden="true" />
          ) : (
            <LocateFixed size={13} aria-hidden="true" />
          )}
        </button>
        {!loading && text && (
          <button
            type="button"
            className="place-input-clear"
            aria-label={`Clear ${label}`}
            onClick={() => {
              setText("");
              setResults([]);
              setOpen(false);
              setQueryActive(false);
              onSelect(null);
            }}
          >
            <X size={12} />
          </button>
        )}
      </div>

      {open && (results.length > 0 || error) && (
        <div className="search-popover" role="listbox" id={listId}>
          {error && <div className="search-error">{error}</div>}
          {results.map((place, index) => (
            <button
              key={place.id}
              id={`${listId}-${index}`}
              type="button"
              role="option"
              aria-selected={index === active}
              className="search-option"
              data-active={index === active}
              onMouseDown={(event) => event.preventDefault()}
              onMouseEnter={() => setActive(index)}
              onClick={() => choose(place)}
            >
              <strong>{place.name}</strong>
              <span>{place.detail}</span>
            </button>
          ))}
          <div className="search-attribution">Search by OpenStreetMap / Nominatim</div>
        </div>
      )}

      {locationError && (
        <span className="place-location-error" role="alert">
          {locationError}
        </span>
      )}

      {!text && (
        <span className="place-hint">
          <Search size={10} />
          Type a place, address or intersection
        </span>
      )}
    </div>
  );
}
