import { useEffect, useState } from "react";

export const splitModels = (text: string): string[] => text.split(",").map((m) => m.trim()).filter(Boolean);

// A comma-separated list of model ids. The text is kept as typed ("a, " is half of "a, b", not the list ["a"]), and
// follows `value` again when that changes from outside (a provider switch puts back another list).
export function ModelListInput({ value, onChange, placeholder, ariaLabel, disabled }: {
  value: string[];
  onChange: (list: string[]) => void;
  placeholder?: string;
  ariaLabel?: string;
  disabled?: boolean;
}) {
  const [text, setText] = useState(value.join(", "));
  const joined = value.join(",");
  useEffect(() => {
    if (splitModels(text).join(",") !== joined) setText(value.join(", "));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [joined]);
  return (
    <input className="input font-mono text-xs" value={text} disabled={disabled} aria-label={ariaLabel}
      placeholder={placeholder}
      onChange={(e) => { setText(e.target.value); onChange(splitModels(e.target.value)); }} />
  );
}
