import { useRef, useState } from "react";

interface Props {
  onSubmit: (file: File, language: string) => void;
  pending: boolean;
  error: string | null;
}

/**
 * The upload control.
 *
 * Constraints are stated before the file is chosen rather than returned as a rejection
 * afterwards. The API enforces them either way — PDF magic bytes, 10 MB, 40 pages — but
 * a limit a person only learns by tripping over it is a limit that was not communicated.
 */
export default function Upload({ onSubmit, pending, error }: Props) {
  const [file, setFile] = useState<File | null>(null);
  const [language, setLanguage] = useState("en");
  const [dragging, setDragging] = useState(false);
  const input = useRef<HTMLInputElement>(null);

  function take(chosen: File | undefined) {
    if (chosen) setFile(chosen);
  }

  return (
    <form
      className="upload"
      onSubmit={(event) => {
        event.preventDefault();
        if (file) onSubmit(file, language);
      }}
    >
      <div
        className={`upload__drop${dragging ? " upload__drop--over" : ""}`}
        onDragOver={(event) => {
          event.preventDefault();
          setDragging(true);
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={(event) => {
          event.preventDefault();
          setDragging(false);
          take(event.dataTransfer.files[0]);
        }}
      >
        <input
          ref={input}
          id="spec-file"
          type="file"
          accept="application/pdf,.pdf"
          className="upload__input"
          onChange={(event) => take(event.target.files?.[0])}
        />
        <label htmlFor="spec-file" className="upload__label">
          {file ? file.name : "Choose a specification sheet, or drop one here"}
        </label>
        <p className="upload__hint">PDF, up to 10 MB and 40 pages.</p>
      </div>

      <div className="upload__actions">
        <label className="field">
          <span className="field__label">Language</span>
          <select value={language} onChange={(event) => setLanguage(event.target.value)}>
            <option value="en">English</option>
            <option value="de">German</option>
          </select>
        </label>

        <button type="submit" className="button" disabled={!file || pending}>
          {pending ? "Submitting…" : "Run compliance check"}
        </button>
      </div>

      {error ? <p className="notice notice--error">{error}</p> : null}
    </form>
  );
}
